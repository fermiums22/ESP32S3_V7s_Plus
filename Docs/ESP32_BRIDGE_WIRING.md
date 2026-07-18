# Temporary ESP32-S3 to ESP32 bridge wiring

This is the temporary workbench connection between the YD-ESP32-23
ESP32-S3-N16R8 board and the ESP32-WROOM-32D board. Both boards are powered
from their own USB connectors. Do not connect their 5 V, VIN, or 3.3 V rails.

| Wire color | YD ESP32-S3 | Direction | ESP32-WROOM | Function |
|---|---:|:---:|---:|---|
| Orange | GPIO5 | <- | GPIO27 | I2S BCLK |
| Yellow | GPIO6 | <- | GPIO14 | I2S LRCLK / WS |
| Purple | GPIO7 | -> | GPIO32 | I2S PCM data |
| Blue | GPIO17 TX | -> | GPIO16 RX | Modbus RTU S3 to ESP32 |
| Green | GPIO18 RX | <- | GPIO17 TX | Modbus RTU ESP32 to S3 |
| Red, RESET | GPIO8, open-drain | -> | EN | ESP32 reset, active low |
| Red, BOOT | GPIO9, open-drain | -> | GPIO0 | ESP32 download boot, active low |
| Ground wire(s) | GND | -- | GND | Common signal ground |

Use a 1 kOhm series resistor in each RESET and BOOT line. The photographed
wiring visibly has a resistor in one red line; verify the second line before
enabling S3 outputs. S3 GPIO8 and GPIO9 must start as inputs/high impedance and
later operate only as open-drain outputs.

The ESP32-WROOM is the I2S RX clock master and Modbus RTU server. The S3 is the
I2S TX slave and Modbus RTU client. I2S uses signed 16-bit stereo PCM; Modbus
uses 115200 baud, 8N1.

## Check before applying power

1. With both USB cables disconnected, verify continuity from S3 GND to ESP32
   GND.
2. Verify that neither ground wire is connected to the ESP32 `VIN/5V` pin.
3. Verify orange/yellow/purple at ESP32 GPIO27/GPIO14/GPIO32 respectively.
4. Verify crossed UART: S3 GPIO17 to ESP32 GPIO16, and S3 GPIO18 to ESP32
   GPIO17.
5. Verify S3 GPIO8 goes to ESP32 EN and S3 GPIO9 goes to ESP32 GPIO0.
6. Power each board from its own USB connector; never join the board power
   rails in this temporary setup.

The wire colors document the photographed temporary harness only and are not
a permanent product wiring standard.
