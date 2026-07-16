# GoPro HERO12 BLE ownership

The ESP32-S3 is the single persistent Bluetooth Low Energy controller for the
GoPro HERO12. Home Assistant and future AI code send camera commands through
the standard ESPHome entities; they must not open a second BLE connection to
the camera. HERO12 accepts only one BLE client at a time.

This path is independent of Wi-Fi. Losing Home Assistant or the access point
does not prevent the ESP32 from controlling the camera. Video streaming and
media transfer still require Wi-Fi and go directly between GoPro and the
server; they are not proxied through ESP32.

## First pairing

1. Find the HERO12 BLE MAC address and put it in local `secrets.yaml` as
   `gopro_ble_mac`. The placeholder `00:00:00:00:00:00` cannot connect.
2. On GoPro open `Connections` and start pairing a new device.
3. Reboot or OTA the ESP32 while the camera is on and nearby.
4. Confirm that `GoPro BLE connected` becomes ON and `GoPro status` becomes
   `ready`. Pairing completion is sent using the official Open GoPro network
   management characteristic. The camera remembers the ESP32 identity until a
   camera factory reset.

Do not pair GoPro Quik at the same time. To change the BLE owner, disconnect the
current owner first, then place the camera in pairing mode again.

## Home Assistant and AI contract

All entities belong to the ESPHome device `V7s Plus`:

| Entity | Direction | Meaning |
|---|---|---|
| `GoPro BLE connected` | GoPro -> HA | Command channel is ready |
| `GoPro battery` | GoPro -> HA | Internal battery percentage |
| `GoPro status` | GoPro -> HA | Connection, recording, busy or thermal state |
| `GoPro recording` | bidirectional | Starts/stops shutter encoding |
| `GoPro locate` | bidirectional | Enables the camera locate beeper |
| `GoPro sleep` | HA -> GoPro | Puts the camera into BLE-connectable sleep |
| `GoPro refresh state` | HA -> GoPro | Immediately polls important statuses |

Future local robot behavior must call the same `GoProBLE` methods instead of
writing GATT directly. This preserves a single command queue and prevents HA
and autonomous behavior from racing each other.

## Runtime behavior

- ESPHome reconnects automatically to the configured MAC.
- Each connection re-subscribes to Open GoPro response notifications; GoPro
  does not cache subscriptions.
- A keep-alive is sent every 3 seconds while connected.
- Battery, encoding, busy and overheating states are polled without blocking
  the ESPHome main loop.
- Commands use the official Open GoPro characteristics `GP-0072` through
  `GP-0077`. Locate uses the legacy Open GoPro command `0x16`, retained by
  HERO12; it must be verified on the installed camera firmware.

GoPro Labs screen messages are not part of the BLE API. They will require the
camera's Wi-Fi/HTTP path later; normal camera control remains available over
BLE when Wi-Fi is down.
