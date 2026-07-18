| Signal | Temporary S3 GPIO | Direction | ESP32-WROOM GPIO |
|---|---:|:---:|---:|
| I2S BCLK | 5 | <- | 27 |
| I2S LRCLK / WS | 6 | <- | 14 |
| I2S DATA | 7 | -> | 32 |
| Modbus UART TX | 17 | -> | 16 (RX) |
| Modbus UART RX | 18 | <- | 17 (TX) |
| RESET | 8 | -> | EN |
| BOOT | 9 | <- | 0 |
| Ground | GND | -- | GND |

| Function | ESP32-S3 GPIO | Connection |
|---|---:|---|
| UART TX | 42 | STM32 UART RX |
| UART RX | 41 | STM32 UART TX |
| STM BOOT0 | 1 | 1 kOhm series; 10 kOhm pull-down at STM32 |
| STM NRST | 2 | Open-drain; 1 kOhm series; STM32 pull-up remains fitted |

## Audio path

Wi-Fi audio is decoded on ESP32-S3, sent as signed 16-bit stereo PCM at
44.1 kHz over I2S, received by ESP32-WROOM, and encoded as Bluetooth Classic
A2DP for JBL Go 3.

The default offline track is physically stored on ESP32-S3 in the 6 MB SPIFFS
partition `audio` at flash offset `0xA00000` as `/Balensiaga.mp3`. Use
`flash_audio.bat COM23` to back up and update only this partition. The generated
image and the pre-flash backup remain under the ignored `.esphome` directory.

When no ESPHome API client with state subscription is connected, JBL Play/Pause
increments Modbus holding register 2 on ESP32-WROOM. ESP32-S3 uses that request
to start, pause, or resume the local track. When Home Assistant is connected,
Wi-Fi media playback is handled by `media_player.v7s_plus_wifi_audio`.

## Audio stability baseline

With Wi-Fi, BLE, Bluetooth Classic A2DP, and continuous PCM active, the
ESP32-WROOM currently has about 35 kB free heap, a 13.8 kB largest free block,
and roughly 60% heap fragmentation. Audio testing must watch the debug sensors,
reset reason, loop time, both `PCM 30 s` counters, and underruns. While JBL A2DP
is connected, the ESP32-WROOM pauses background BLE scanning to avoid sharing
radio time with the audio stream; scanning resumes after JBL disconnects.
