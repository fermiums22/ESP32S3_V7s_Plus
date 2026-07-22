# Temporary ESP32-S3 to ESP32 bridge wiring

> The old GPIO5/GPIO6/GPIO7 PCM connection has been removed. UART, RESET,
> BOOT, and ground remain in use for the GoPro/Modbus bridge.

This is the temporary workbench connection between the YD-ESP32-23
ESP32-S3-N16R8 board and the ESP32-WROOM-32D board. Both boards are powered
from their own USB connectors. Do not connect their 5 V, VIN, or 3.3 V rails.

| Wire color | YD ESP32-S3 | Direction | ESP32-WROOM | Function |
|---|---:|:---:|---:|---|
| Blue | GPIO17 TX | -> | GPIO16 RX | Modbus RTU S3 to ESP32 |
| Green | GPIO18 RX | <- | GPIO17 TX | Modbus RTU ESP32 to S3 |
| Red, RESET | GPIO8, open-drain | -> | EN | ESP32 reset, active low |
| Red, BOOT | GPIO9, open-drain | -> | GPIO0 | ESP32 download boot, active low |
| Ground wire(s) | GND | -- | GND | Common signal ground |

Use a 1 kOhm series resistor in each RESET and BOOT line. The photographed
wiring visibly has a resistor in one red line; verify the second line before
enabling S3 outputs. S3 GPIO8 and GPIO9 must start as inputs/high impedance and
later operate only as open-drain outputs.

The ESP32-WROOM remains the Modbus RTU server and the S3 remains the Modbus RTU
client. Modbus uses 115200 baud, 8N1. Audio no longer passes through this link.

## Check before applying power

1. With both USB cables disconnected, verify continuity from S3 GND to ESP32
   GND.
2. Verify that neither ground wire is connected to the ESP32 `VIN/5V` pin.
3. Verify that the old orange/yellow/purple I2S wires are disconnected.
4. Verify crossed UART: S3 GPIO17 to ESP32 GPIO16, and S3 GPIO18 to ESP32
   GPIO17.
5. Verify S3 GPIO8 goes to ESP32 EN and S3 GPIO9 goes to ESP32 GPIO0.
6. Power each board from its own USB connector; never join the board power
   rails in this temporary setup.

The wire colors document the photographed temporary harness only and are not
a permanent product wiring standard.
