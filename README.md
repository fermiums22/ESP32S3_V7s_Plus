# ESP32-S3 V7s Plus

Главный контроллер робота `v7s-plus` на ESP32-S3:

- Wi-Fi, Home Assistant API и OTA;
- Modbus RTU client для STM32 и ESP32 audio bridge;
- Wi-Fi audio через ESPHome media player и Home Assistant;
- I2S slave TX на GPIO5/GPIO6/GPIO7;
- управление Wi-Fi audio bridge через Modbus holding register 1.

Bluetooth и GoPro BLE на S3 не используются.

## Сборка

```bat
setup_esphome.bat
compile.bat
factory_flash.bat COM23
flash.bat v7s-plus.local
logs.bat v7s-plus.local
flash_audio.bat COM23
```

Основной конфиг: `v7s-plus.yaml`. ESPHome закреплён в `requirements.txt`.
Схема межплатных соединений: `Docs/ESP32_BRIDGE_WIRING.md`.
STM32 и его обновление: `Docs/STM32_UPDATE.md`.

## Автономный трек

`filesystem/Balensiaga.mp3` хранится в отдельном SPIFFS-разделе `audio` размером
6 MB. `flash_audio.bat COM23` сначала считывает резервную копию раздела в
`.esphome/backups`, затем записывает и проверяет новый образ. При отсутствии
подключения Home Assistant кнопка Play/Pause на JBL запускает или приостанавливает
этот локальный трек через ESP32 audio bridge.

Для воспроизведения по Wi-Fi используйте сущность Home Assistant
`media_player.v7s_plus_wifi_audio` и файлы из `/media` Home Assistant.
