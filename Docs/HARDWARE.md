# Hardware notes

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

## Audio output

The first audio option is a MAX98357A-compatible mono I2S class-D amplifier:

| Signal | ESP32-S3 GPIO |
|---|---:|
| BCLK | 5 |
| LRCLK / WS | 6 |
| DIN | 7 |
| SD / enable | 4 |

Power the amplifier from a clean 5 V rail, place bulk decoupling near it, and
use a 4 Ohm speaker rated for at least 3 W. GPIO4 disables the amplifier when
idle to avoid hiss. The microphone remains in GoPro HERO12 and reaches the
server over Wi-Fi; while the robot speaks, server-side listening is paused to
avoid acoustic feedback.

