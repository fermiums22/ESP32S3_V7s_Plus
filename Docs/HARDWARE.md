| Signal | Temporary S3 GPIO | Direction | ESP32-WROOM GPIO |
|---|---:|:---:|---:|
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

The planned direct S3 microphone and amplifier wiring is documented in
[S3_AUDIO_WIRING.md](S3_AUDIO_WIRING.md).

Wi-Fi audio is decoded on ESP32-S3 and sent as signed 16-bit stereo PCM at
44.1 kHz from I2S1 directly to the planned MAX I2S amplifier. ESP32-S3 is the
clock master on GPIO5/GPIO6 and sends data on GPIO7. The former I2S slave path
to ESP32-WROOM and Bluetooth Classic/JBL is retired.

I2S0 is reserved for stereo PDM RX from the two T3902 microphones on GPIO10
and GPIO11. The ESP32-WROOM Modbus/GoPro bridge remains connected for camera
control, but no longer participates in audio playback.

The default offline track is physically stored on ESP32-S3 in the 6 MB SPIFFS
partition `audio` at flash offset `0xA00000` as `/Balensiaga.mp3`. Use
`flash_audio.bat COM23` to back up and update only this partition. The generated
image and the pre-flash backup remain under the ignored `.esphome` directory.

Wi-Fi and local media playback is handled directly by
`media_player.v7s_plus_wifi_audio` on ESP32-S3.

## Audio stability baseline

Audio testing must watch the S3 debug sensors, reset reason, loop time,
`PCM 30 s` counters, microphone overruns, and speaker underruns.
