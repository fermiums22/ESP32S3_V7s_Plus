"""Download a firmware artifact from ESPHome Device Builder's current API."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.request import urlopen

import websockets


async def command(connection, message_id: int, name: str, args: dict) -> object:
    await connection.send(
        json.dumps({"command": name, "message_id": str(message_id), "args": args})
    )
    async for raw_message in connection:
        message = json.loads(raw_message)
        if str(message.get("message_id")) != str(message_id):
            continue
        if "error_code" in message:
            raise RuntimeError(f"{message['error_code']}: {message.get('details', '')}")
        if "result" in message:
            return message["result"]
    raise RuntimeError("Device Builder WebSocket closed")


async def download(base_url: str, configuration: str, output: Path) -> None:
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    async with websockets.connect(ws_url, origin=None, ping_interval=None) as connection:
        server_info = json.loads(await connection.recv())
        if server_info.get("requires_auth"):
            raise RuntimeError("Device Builder requires authentication")
        binaries = await command(
            connection, 1, "firmware/get_binaries", {"configuration": configuration}
        )
        candidates = []
        if isinstance(binaries, dict):
            candidates = binaries.get("binaries") or binaries.get("downloads") or []
        elif isinstance(binaries, list):
            candidates = binaries
        if not candidates:
            raise RuntimeError(f"No firmware binaries returned: {binaries!r}")
        selected = next(
            (
                item
                for item in candidates
                if "factory" in str(item.get("file", "")).casefold()
                or "factory" in str(item.get("name", "")).casefold()
            ),
            candidates[0],
        )
        file_name = str(selected.get("file") or selected.get("path") or "")
        if not file_name:
            raise RuntimeError(f"Binary has no file field: {selected!r}")
        token_data = await command(
            connection,
            2,
            "firmware/download_token",
            {"configuration": configuration, "file": file_name},
        )
    if not isinstance(token_data, dict) or not token_data.get("token"):
        raise RuntimeError(f"No download token returned: {token_data!r}")
    url = f"{base_url}/api/firmware/download?token={token_data['token']}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=60) as response:
        output.write_bytes(response.read())
    print(f"saved {output} ({output.stat().st_size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("configuration")
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-url", default="http://192.168.0.4:6052")
    args = parser.parse_args()
    asyncio.run(download(args.base_url, args.configuration, args.output))


if __name__ == "__main__":
    main()
