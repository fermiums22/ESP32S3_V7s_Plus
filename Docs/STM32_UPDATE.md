# STM32 communication and update

The STM32F071 is a Modbus RTU slave at address `1`. The application link uses
USART1 at `921600 8N1`: ESP32 GPIO42 TX to STM32 PA10 RX, and ESP32 GPIO41 RX
from STM32 PA9 TX.

Home Assistant talks only to the ESPHome native API. Standard ESPHome Modbus
Controller entities expose STM32 sensors, motor commands and safety controls
inside the `V7s Plus` device. No custom Home Assistant integration is needed.

`compile.bat` first builds the adjacent `..\V7s_Plus` STM32 project and embeds
`Debug\V7s_Plus.bin` in the ESP32 image. STM32CubeIDE 2.0.0 is expected at the
default `C:\ST` path. For another install path, set `STM32CUBEIDE` to the full
path of `stm32cubeidec.exe`.

Update sequence:

1. Change and test the STM32 C code.
2. Run ESP32 `compile.bat`, then upload the ESP32 image by OTA.
3. In Home Assistant press `Flash STM32 firmware` on the `V7s Plus` device.

The ESP32 disables the motors, pauses Modbus, raises STM32 BOOT0 on GPIO1,
pulses the open-drain NRST on GPIO2, writes and verifies the embedded image via
the STM32 ROM bootloader at `230400 8E1`, then restarts the application and
resumes Modbus. Flash progress and status are exposed as HA entities.

The complete register map is in `..\V7s_Plus\docs\modbus-registers.md`.
