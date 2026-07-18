# ESP32-S3 V7s Plus

Главный контроллер робота `v7s-plus` на ESP32-S3:

- Wi-Fi, Home Assistant API и OTA;
- Modbus RTU client для STM32 и ESP32 audio bridge;
- сетевой PCM-вход `44100 Hz / S16LE / stereo` на TCP 8765;
- I2S slave TX на GPIO5/GPIO6/GPIO7;
- управление Wi-Fi audio bridge через Modbus holding register 1.

Bluetooth и GoPro BLE на S3 не используются.

## Сборка

```bat
setup_esphome.bat
compile.bat
factory_flash.bat COM15
flash.bat COM15
logs.bat COM15
```

Основной конфиг: `v7s-plus.yaml`. ESPHome закреплён в `requirements.txt`.
Схема межплатных соединений: `Docs/ESP32_BRIDGE_WIRING.md`.
STM32 и его обновление: `Docs/STM32_UPDATE.md`.

Для временной проверки PCM-входа:

```bat
.venv\Scripts\python tools\stream_mp3.py track.mp3 --host 192.168.0.101
```
