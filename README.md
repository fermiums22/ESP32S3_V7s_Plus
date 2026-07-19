# ESP32-S3 V7s Plus

Главный контроллер робота `v7s-plus` на ESP32-S3. Он связывает Home Assistant,
STM32F071 и отдельный ESP32-WROOM audio/GoPro bridge по Modbus RTU с адресом `2`.

Главные рабочие инструкции находятся в [Docs/OPERATIONS.md](Docs/OPERATIONS.md):

- как отдельно обновлять STM32 через уже установленный S3;
- как собирать и обновлять ESP32-S3 и ESP32 audio bridge;
- как GoPro управляется ESP32-WROOM без Windows bridge, как устроены видео,
  звук и распознавание речи;
- какие части являются рабочими, а какие требуют физического доступа к камере.

`compile.bat` собирает **только ESP32-S3**. Обычное обновление STM32 не требует
ни пересборки, ни OTA S3: собрать STM32, временно загрузить его `.bin` на S3 и
нажать кнопку прошивки в Home Assistant.

## Частые команды

```bat
:: ESP32-S3
setup_esphome.bat
compile.bat
flash.bat v7s-plus.local
logs.bat v7s-plus.local

:: STM32 — только сборка, S3 не затрагивается
build_stm32.bat

:: ESP32-WROOM — запускать из D:\w_space\ESP32_V7s_Plus,
:: используя только его собственную сборку и OTA
```

Основной конфиг S3: `v7s-plus.yaml`. Секреты находятся только в игнорируемом
`secrets.yaml`; образец ключей — `secrets.example.yaml`.

Схема соединений плат: [Docs/ESP32_BRIDGE_WIRING.md](Docs/ESP32_BRIDGE_WIRING.md).
Карта железа и звуковой тракт: [Docs/HARDWARE.md](Docs/HARDWARE.md).

## Репозиторные правила

В репозитории не хранятся прошивки, дампы, тестовые обходы, HA-токены,
пароли Wi-Fi и временные состояния OpenGoPro. Перед коммитом проверяются
`git diff --check`, сборка изменённой прошивки и отсутствие таких файлов в
`git status`.
