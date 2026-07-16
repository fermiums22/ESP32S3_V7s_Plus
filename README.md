# ESP32S3 V7s Plus

Контроллер верхнего уровня домашнего робота на AI-S3 (`ESP32-S3-WROOM-1`).
ESPHome связывает устройство с Home Assistant, принимает команды и OTA,
обслуживает STM32 по UART и I2S-аудиовыход штатными компонентами. Внешние
интеграции и кастомные компоненты не используются. GoPro HERO12 передаёт видео
и микрофон на сервер напрямую по Wi-Fi.

## Локальная сборка

В репозитории закреплён ESPHome Core `2026.6.5`. Он использует нативный
ESP-IDF toolchain и сам загружает ту же версию framework и инструментов, что
ESPHome той же версии в Home Assistant. PlatformIO не используется.

На новом Windows-компьютере нужны Python 3.11–3.14 и Git:

```bat
setup_esphome.bat
compile.bat
factory_flash.bat COM9
flash.bat COM9
logs.bat COM9
```

Локальные пакеты находятся в `.venv`, SDK и результат сборки — в `.esphome`;
в Git они не попадают. Для полного совпадения версия ESPHome Add-on в HA должна
быть `2026.6.5`. При обновлении HA меняется одна строка в `requirements.txt`,
после чего повторно запускается `setup_esphome.bat`.

Скопируйте `secrets.example.yaml` в `secrets.yaml` и заполните локальные ключи.
`secrets.yaml` игнорируется Git. В VS Code доступны задачи `ESPHome: setup`,
`ESPHome: compile`, `ESPHome: factory flash USB`, `ESPHome: OTA` и
`ESPHome: logs`.

## Конфигурация

Основной файл — `v7s-plus.yaml`. Это обычная конфигурация ESPHome: её можно
собирать локальным Core или тем же ESPHome Add-on в Home Assistant. Сейчас в
ней включены Wi-Fi, native API, OTA, лог через native USB Serial/JTAG и базовая
диагностика. Распиновка STM32, исправление BOOT и I2S описаны в
`Docs/HARDWARE.md`.

`compile.bat` создаёт обычный OTA-образ и полный `factory.bin`, содержащий
bootloader, partition table и приложение — такой же комплект формирует HA.
Готовые файлы находятся в `.esphome/build/v7s-plus/build/` под именами
`firmware.ota.bin`, `firmware.factory.bin` и `firmware.elf`.
Первый запуск и восстановление выполняются `factory_flash.bat` по USB.
Последующие версии можно загружать `flash.bat` по USB/OTA либо из HA.
