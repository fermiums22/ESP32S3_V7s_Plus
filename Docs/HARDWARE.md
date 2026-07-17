# Hardware notes

## Temporary workbench board

**Temporary setup at work:** YD-ESP32-23 / YD-ESP32-S3-compatible board with
an ESP32-S3-WROOM-1-N16R8 module (16 MB flash, 8 MB octal PSRAM). This board is
used only until the AI-S3 development board intended for the robot is available
again. It is not the final hardware revision.

The board has separate `COM` (CH343 USB-to-UART) and `USB` (native USB/JTAG)
USB-C connectors. GPIO35, GPIO36, and GPIO37 are occupied by octal flash/PSRAM
and must not be used. The downloaded V1.4 schematic is the closest official
YD-ESP32-S3 reference; power jumpers and component values may differ on a clone.

### Temporary ESP32-S3 to ESP32 audio bridge

The complete color-coded harness and pre-power checks are documented in
[`ESP32_BRIDGE_WIRING.md`](ESP32_BRIDGE_WIRING.md).

| Signal | Temporary S3 GPIO | Direction | ESP32-WROOM GPIO |
|---|---:|:---:|---:|
| I2S BCLK | 5 | <- | 27 |
| I2S LRCLK / WS | 6 | <- | 14 |
| I2S DATA | 7 | -> | 32 |
| Modbus UART TX | 17 | -> | 16 (RX) |
| Modbus UART RX | 18 | <- | 17 (TX) |
| Ground | GND | -- | GND |

The ESP32-WROOM is the I2S clock master and Modbus RTU slave. The temporary S3
is the I2S data source and Modbus RTU client. GoPro commands and state are
reserved for this UART bridge so Bluetooth remains disabled on the S3. If both boards are
powered by USB, do not connect their 5 V or 3.3 V rails.

## AI-S3 boot strap fix

The AI-S3 schematic has no external pull-up on GPIO0. The board relies on the
ESP32-S3 internal weak pull-up while the BOOT button and the CH343 auto-program
circuit pull GPIO0 low. This can cause an unintended download boot.

Installed fix: **9.1 kOhm from GPIO0 to 3.3 V**. Do not add a capacitor to
GPIO0. Use the connector marked `USB` for native USB Serial/JTAG; the `COM`
connector goes through CH343 and its DTR/RTS auto-program circuit.

## STM32 service interface

| Function | ESP32-S3 GPIO | Connection |
|---|---:|---|
| UART TX | 42 | STM32 UART RX |
| UART RX | 41 | STM32 UART TX |
| STM BOOT0 | 1 | 1 kOhm series; 10 kOhm pull-down at STM32 |
| STM NRST | 2 | Open-drain; 1 kOhm series; STM32 pull-up remains fitted |

GPIO1 and GPIO2 can produce a short low-level glitch during ESP32-S3 power-up.
The proposed external states keep STM32 in normal boot and make a brief reset
at common power-up harmless.
