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
