# ESP32-S3 board schematics

## Temporary YD-ESP32-23 board

`YD-ESP32-S3-SCH-V1.4.pdf` is the official YD-ESP32-S3 V1.4 reference
schematic. The photographed temporary workbench board is marked YD-ESP32-23
and carries an ESP32-S3-WROOM-1-N16R8 module. It is used temporarily at work;
the AI-S3 board remains the intended robot hardware.

Source:
https://github.com/vcc-gnd/YD-ESP32-S3/tree/main/5-public-YD-ESP32-S3-Hardware%20info

SHA-256:
`EC1844F0E7D736B7CCCA556E6CEC6F1876F5EE7D661B249D0F780E4124790609`

The photographed PCB may be a clone or a different board revision. Verify
power jumpers and rail voltages before powering external hardware.

## Intended AI-S3 board

`AI-S3_ESP32-S3_Dev_Board_Schematic.pdf` is a third-party schematic matching
the intended AI-S3 development board.

Source:
https://github.com/Xylopyrographer/BitsNThings/tree/main/AI-S3%20ESP32-S3%20Dev%20Board

SHA-256:
`0C47A30B5FAF0D5F939CCD91503E332FA14B44DB77E1A0766B7C36637CDF4FB1`

Before assigning GPIOs or changing power circuitry, verify critical signals
against the physical board and the official ESP32-S3-WROOM-1 documentation.
