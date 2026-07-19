"""Authenticated Windows BLE bridge for the paired GoPro HERO12.

The camera publishes video and audio straight to HA/go2rtc over RTMP.  This
process never proxies media; it only exposes real GoPro BLE actions to Home
Assistant while the permanent robot-side BLE controller is being prepared.

Required environment variables:
  GOPRO_BRIDGE_TOKEN          shared secret for HTTP callers
  GOPRO_HOME_WIFI_SSID        5 GHz home-network SSID
  GOPRO_HOME_WIFI_PASSWORD    home-network passphrase

Optional variables:
  GOPRO_BLE_ADDRESS           GoPro address without separators
  GOPRO_RTMP_URL              RTMP destination (default gopro_robot in HA)
  GOPRO_BRIDGE_HOST / _PORT   HTTP listener address and port
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from open_gopro.models import proto
from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattClientCharacteristicConfigurationDescriptorValue,
    GattWriteOption,
)
from winrt.windows.storage.streams import Buffer


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


GOPRO_ADDRESS = int(os.environ.get("GOPRO_BLE_ADDRESS", "DBA3E1603244"), 16)
HA_RTMP_URL = os.environ.get("GOPRO_RTMP_URL", "rtmp://192.168.0.4:1935/gopro_robot")
BIND_HOST = os.environ.get("GOPRO_BRIDGE_HOST", "192.168.0.10")
BIND_PORT = int(os.environ.get("GOPRO_BRIDGE_PORT", "8787"))
TOKEN = required_env("GOPRO_BRIDGE_TOKEN")

CONTROL_SERVICE = "0000fea6-0000-1000-8000-00805f9b34fb"
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"
COMMAND = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"
COMMAND_RESPONSE = "b5f90073-aa8d-11e3-9046-0002a5d5c51b"
QUERY = "b5f90076-aa8d-11e3-9046-0002a5d5c51b"
QUERY_RESPONSE = "b5f90077-aa8d-11e3-9046-0002a5d5c51b"
CAMERA_MANAGEMENT_SERVICE = "b5f90090-aa8d-11e3-9046-0002a5d5c51b"
NETWORK_MANAGEMENT = "b5f90091-aa8d-11e3-9046-0002a5d5c51b"
NETWORK_RESPONSE = "b5f90092-aa8d-11e3-9046-0002a5d5c51b"
LOCK = threading.Lock()


class GoProError(RuntimeError):
    """A camera command could not be delivered or was rejected."""


class PacketAssembler:
    """Reassemble OpenGoPro BLE response fragments."""

    def __init__(self) -> None:
        self._payload = bytearray()
        self._remaining: int | None = None

    def feed(self, data: bytes) -> bytes | None:
        buf = bytearray(data)
        if not buf:
            return None
        if buf[0] & 0x80:
            if self._remaining is None:
                return None
            buf = buf[1:]
        else:
            packet_type = (buf[0] & 0x60) >> 5
            self._payload = bytearray()
            if packet_type == 0:
                self._remaining = buf[0] & 0x1F
                buf = buf[1:]
            elif packet_type == 1 and len(buf) >= 2:
                self._remaining = ((buf[0] & 0x1F) << 8) + buf[1]
                buf = buf[2:]
            elif packet_type == 2 and len(buf) >= 3:
                self._remaining = (buf[1] << 8) + buf[2]
                buf = buf[3:]
            else:
                return None
        self._payload.extend(buf)
        self._remaining -= len(buf)
        if self._remaining == 0:
            complete = bytes(self._payload)
            self._payload = bytearray()
            self._remaining = None
            return complete
        return None


def fragments(payload: bytes):
    """Encode an OpenGoPro message as <=20 byte GATT writes."""
    if len(payload) >= 8191:
        raise GoProError("BLE payload unexpectedly large")
    header = (len(payload) | 0x2000).to_bytes(2, "big")
    offset = 0
    first = True
    while offset < len(payload):
        prefix = header if first else b"\x80"
        first = False
        take = min(20 - len(prefix), len(payload) - offset)
        yield prefix + payload[offset : offset + take]
        offset += take


class GoProBle:
    def __init__(self) -> None:
        self._device = None
        self._command = self._query = None
        self._command_response = self._query_response = None
        self._network_command = self._network_response = None
        self._command_token = self._query_token = self._network_token = None
        self._commands: asyncio.Queue[bytes] = asyncio.Queue()
        self._queries: asyncio.Queue[bytes] = asyncio.Queue()
        self._network: asyncio.Queue[bytes] = asyncio.Queue()
        self._command_assembler = PacketAssembler()
        self._query_assembler = PacketAssembler()
        self._network_assembler = PacketAssembler()

    async def __aenter__(self):
        self._device = await BluetoothLEDevice.from_bluetooth_address_async(GOPRO_ADDRESS)
        services = await self._device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        control = next((s for s in services.services if str(s.uuid).lower() == CONTROL_SERVICE), None)
        if control is None:
            raise GoProError("GoPro BLE control is unavailable; open Connections > Pair/Connect on the camera")
        result = await control.get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        if result.status != 0:
            raise GoProError("GoPro BLE control is access-denied; open Connections > Pair/Connect on the camera")
        chars = {str(c.uuid).lower(): c for c in result.characteristics}
        try:
            self._command = chars[COMMAND]
            self._command_response = chars[COMMAND_RESPONSE]
            self._query = chars[QUERY]
            self._query_response = chars[QUERY_RESPONSE]
        except KeyError as err:
            raise GoProError("GoPro BLE control characteristic is missing") from err

        management = next(
            (s for s in services.services if str(s.uuid).lower() == CAMERA_MANAGEMENT_SERVICE), None
        )
        if management is None:
            raise GoProError("GoPro Wi-Fi management service is unavailable; keep Pair/Connect open")
        result = await management.get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        if result.status != 0:
            raise GoProError("GoPro Wi-Fi management is access-denied; keep Pair/Connect open")
        network_chars = {str(c.uuid).lower(): c for c in result.characteristics}
        try:
            self._network_command = network_chars[NETWORK_MANAGEMENT]
            self._network_response = network_chars[NETWORK_RESPONSE]
        except KeyError as err:
            raise GoProError("GoPro Wi-Fi management characteristic is missing") from err

        def command_callback(_, args) -> None:
            if value := self._command_assembler.feed(bytes(args.characteristic_value)):
                self._commands.put_nowait(value)

        def query_callback(_, args) -> None:
            if value := self._query_assembler.feed(bytes(args.characteristic_value)):
                self._queries.put_nowait(value)

        def network_callback(_, args) -> None:
            if value := self._network_assembler.feed(bytes(args.characteristic_value)):
                self._network.put_nowait(value)

        self._command_token = self._command_response.add_value_changed(command_callback)
        self._query_token = self._query_response.add_value_changed(query_callback)
        self._network_token = self._network_response.add_value_changed(network_callback)
        for characteristic in (self._command_response, self._query_response, self._network_response):
            response = await characteristic.write_client_characteristic_configuration_descriptor_with_result_async(
                GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
            )
            if response.status != 0:
                raise GoProError("Could not subscribe to a GoPro BLE response")
        return self

    async def __aexit__(self, *_exc_info) -> None:
        for characteristic, token in (
            (self._command_response, self._command_token),
            (self._query_response, self._query_token),
            (self._network_response, self._network_token),
        ):
            if characteristic is None:
                continue
            try:
                await characteristic.write_client_characteristic_configuration_descriptor_with_result_async(
                    GattClientCharacteristicConfigurationDescriptorValue.NONE
                )
                if token is not None:
                    characteristic.remove_value_changed(token)
            except Exception:
                pass

    async def _write(self, characteristic, payload: bytes) -> None:
        for packet in fragments(payload):
            buffer = Buffer(len(packet))
            buffer.length = buffer.capacity
            with memoryview(buffer) as view:
                view[:] = packet
            response = await characteristic.write_value_with_result_and_option_async(
                buffer, GattWriteOption.WRITE_WITH_RESPONSE
            )
            if response.status != 0:
                raise GoProError(f"GoPro BLE write failed ({response.status})")

    async def _wait(self, queue: asyncio.Queue[bytes], predicate, timeout: int, label: str) -> bytes:
        deadline = asyncio.get_running_loop().time() + timeout
        while remaining := deadline - asyncio.get_running_loop().time():
            try:
                value = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError as err:
                raise GoProError(f"Timed out waiting for {label}") from err
            if predicate(value):
                return value
        raise GoProError(f"Timed out waiting for {label}")

    async def command(self, payload: bytes, predicate, label: str, timeout: int = 8) -> bytes:
        await self._write(self._command, payload)
        return await self._wait(self._commands, predicate, timeout, label)

    async def query(self, payload: bytes, predicate, label: str, timeout: int = 8) -> bytes:
        await self._write(self._query, payload)
        return await self._wait(self._queries, predicate, timeout, label)

    async def network_command(self, payload: bytes, predicate, label: str, timeout: int = 15) -> bytes:
        await self._write(self._network_command, payload)
        return await self._wait(self._network, predicate, timeout, label)

    async def third_party_and_control(self) -> None:
        ack = await self.command(b"\x50", lambda body: body and body[0] == 0x50, "third-party acknowledgement")
        if len(ack) < 2 or ack[1] != 0:
            raise GoProError("GoPro rejected third-party control")
        ack = await self.command(
            b"\xf1\x69\x08\x02", lambda body: body[:2] == b"\xf1\xe9", "external-control acknowledgement"
        )
        result = proto.ResponseGeneric.FromString(ack[2:])
        if not result.HasField("result") or result.result != proto.EnumResultGeneric.RESULT_SUCCESS:
            raise GoProError("GoPro rejected external control claim")

    async def shutter(self, enabled: bool) -> None:
        ack = await self.command(
            bytes((0x01, 0x01, int(enabled))), lambda body: body and body[0] == 0x01, "shutter acknowledgement"
        )
        if len(ack) < 2 or ack[1] != 0:
            raise GoProError("GoPro rejected shutter command")

    async def livestream_status(self) -> proto.NotifyLiveStreamStatus:
        body = await self.query(
            b"\xf5\x74\x08\x01", lambda value: value[:2] in (b"\xf5\xf4", b"\xf5\xf5"), "livestream status"
        )
        return proto.NotifyLiveStreamStatus.FromString(body[2:])

    async def start_stream(self) -> dict:
        await self.third_party_and_control()
        status = await self.livestream_status()
        if status.HasField("live_stream_status") and status.live_stream_status == proto.EnumLiveStreamStatus.LIVE_STREAM_STATE_STREAMING:
            return {"stream": "already_streaming"}
        await self.shutter(False)
        config = proto.RequestSetLiveStreamMode(
            url=HA_RTMP_URL,
            encode=True,
            window_size=12,
            minimum_bitrate=3500,
            maximum_bitrate=5000,
            starting_bitrate=4500,
            lens=0,
        )
        ack = await self.command(
            b"\xf1\x79" + config.SerializeToString(),
            lambda body: body[:2] == b"\xf1\xf9",
            "livestream configuration acknowledgement",
        )
        result = proto.ResponseGeneric.FromString(ack[2:])
        if not result.HasField("result") or result.result != proto.EnumResultGeneric.RESULT_SUCCESS:
            raise GoProError("GoPro rejected RTMP configuration")

        def ready(body: bytes) -> bool:
            return body[:2] in (b"\xf5\xf4", b"\xf5\xf5") and (
                proto.NotifyLiveStreamStatus.FromString(body[2:]).live_stream_status
                == proto.EnumLiveStreamStatus.LIVE_STREAM_STATE_READY
            )

        await self._wait(self._queries, ready, 30, "GoPro livestream READY")
        await asyncio.sleep(2)
        await self.shutter(True)
        return {"stream": "starting"}

    async def stop_stream(self) -> dict:
        await self.third_party_and_control()
        await self.shutter(False)
        return {"stream": "stopped"}

    async def wake(self) -> dict:
        await self.third_party_and_control()
        await self.shutter(False)
        return {"camera": "awake"}

    async def sleep(self) -> dict:
        await self.third_party_and_control()
        await self.shutter(False)
        ack = await self.command(b"\x05", lambda body: body and body[0] == 0x05, "sleep acknowledgement")
        if len(ack) < 2 or ack[1] != 0:
            raise GoProError("GoPro rejected sleep command")
        return {"camera": "sleeping"}

    async def connect_home_wifi(self) -> dict:
        ssid = required_env("GOPRO_HOME_WIFI_SSID")
        password = required_env("GOPRO_HOME_WIFI_PASSWORD")
        request = proto.RequestConnectNew(ssid=ssid, password=password, bypass_eula_check=True)
        ack = await self.network_command(
            b"\x02\x05" + request.SerializeToString(),
            lambda body: body[:2] == b"\x02\x85",
            "Wi-Fi provisioning acknowledgement",
        )
        response = proto.ResponseConnectNew.FromString(ack[2:])
        if not response.HasField("result") or response.result != proto.EnumResultGeneric.RESULT_SUCCESS:
            raise GoProError("GoPro rejected home Wi-Fi provisioning")
        return {"wifi": "provisioning", "ssid": ssid}


async def read_battery() -> dict:
    device = await BluetoothLEDevice.from_bluetooth_address_async(GOPRO_ADDRESS)
    services = await device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
    service = next((s for s in services.services if str(s.uuid).lower() == BATTERY_SERVICE), None)
    if service is None:
        raise GoProError("GoPro battery service unavailable")
    result = await service.get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
    battery = next((c for c in result.characteristics if str(c.uuid).lower() == BATTERY_LEVEL), None)
    if battery is None:
        raise GoProError("GoPro battery level unavailable")
    value = await battery.read_value_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
    if value.status != 0 or not value.value:
        raise GoProError("GoPro battery read failed")
    return {"battery_percent": int(bytes(value.value)[0])}


async def execute(action: str) -> dict:
    if action == "battery":
        return await read_battery()
    async with GoProBle() as gopro:
        if action == "stream_start":
            return await gopro.start_stream()
        if action == "stream_stop":
            return await gopro.stop_stream()
        if action == "camera_on":
            return await gopro.wake()
        if action == "camera_off":
            return await gopro.sleep()
        if action == "wifi_home":
            return await gopro.connect_home_wifi()
        if action == "health":
            return {"control": "ready"}
    raise GoProError("Unknown GoPro bridge action")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def _reply(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("X-GoPro-Bridge-Token") == TOKEN

    def _run(self, action: str) -> None:
        if not self._authorized():
            self._reply(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            with LOCK:
                result = asyncio.run(execute(action))
            self._reply(HTTPStatus.OK, result)
        except GoProError as err:
            self._reply(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(err)})
        except Exception as err:
            self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(err).__name__, "detail": str(err)})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._run("health")
        elif self.path == "/battery":
            self._run("battery")
        else:
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        actions = {
            "/stream/start": "stream_start",
            "/stream/stop": "stream_stop",
            "/camera/on": "camera_on",
            "/camera/off": "camera_off",
            "/wifi/home": "wifi_home",
        }
        action = actions.get(self.path)
        if action is None:
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._run(action)


if __name__ == "__main__":
    ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler).serve_forever()
