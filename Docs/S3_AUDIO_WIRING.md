# ESP32-S3 audio wiring

## Assembled stereo PDM microphone board

Do not identify the fitted microphones as TDK T3902 from this repository. The
Altium symbols for `BM1` and `BM2` still carry the library metadata
`MMICT390200012`, but that part number is known not to describe the microphones
fitted to the assembled board. The wiring below is therefore based on the board
nets and connector labels, not on that stale component name. Update the Altium
BOM/symbol metadata when the exact fitted microphone part number is available.

The assembled board accepts 5 V and generates its own low-noise 3.3 V rail with
`LP2985A-33DBVRM3`. Do not feed 3.3 V into the connector pin marked `5V`.

On the temporary **YD-ESP32-S3 V1.4** workbench board, USB 5 V does not reach
the header's `5V` pin while the solder jumper marked `IN-OUT` is open. The
official schematic shows this jumper bypassing the series protection diode
`D3`. With USB disconnected, close only `IN-OUT` with a solder bridge or a
0-ohm link before using the header as a 5 V output. Do not accidentally bridge
the adjacent `RGB` or `USB-OTG` jumpers. This note is specific to the temporary
YD board; verify the power arrangement again after moving to the intended
AI-S3 board.

### Connection to the ESP32-S3

With the component side facing you, the microphones at the top, and connector
`XP1` at the bottom, pin 1 is the rightmost pin. The front label in the supplied
photo lists the same pins from top to bottom: `GND`, `EN`, `5V`, blank, `SCK`,
`DATA`.

| XP1 pin | Board label | Connect to ESP32-S3 | Purpose |
|---:|---|---|---|
| 1 | `GND` | `GND` | Common power and signal ground |
| 2 | `EN` | `GPIO12` | Active-high enable of the on-board 3.3 V LDO |
| 3 | `5V` | board pin marked `5V` | Input power for the microphone board |
| 4 | no label / NC | leave disconnected | Not connected on the schematic |
| 5 | `SCK` | `GPIO10` | Shared PDM clock from the S3 |
| 6 | `DATA` | `GPIO11` | Shared stereo PDM data to the S3 |

`GPIO12` is configured by `v7s-plus.yaml` as an internal GPIO switch with
`restore_mode: ALWAYS_ON`. Its 3.3 V high level enables the LDO automatically
when ESPHome starts. The fitted 10 kOhm pull-down keeps the microphone board off
while GPIO12 is not yet being driven. Do not connect the microphone-board `EN`
wire to the ESP32 development board's `EN`/reset pin.

Power off both boards before wiring. Use the printed signal labels rather than
jumper-wire colours, leave pin 4 open, and verify approximately 3.3 V on the
microphone supply rail before connecting `SCK` and `DATA`. On the temporary YD
board, also verify approximately 5 V between its header `5V` pin and `GND`
after closing `IN-OUT`.

### Board signal arrangement

Both microphones share `SCK` and one physical `DATA` line. On the current
schematic `BM1` has `SELECT` strapped low and `BM2` has `SELECT` strapped high,
so they transmit in opposite PDM slots. A hardware isolation test on the fitted
board established the actual slot order: removing BM1's DATA link `R1` removed
the signal previously visible in the ESP's right slot. The console therefore
labels the slots `PDM R/BM1` and `PDM L/BM2`. This is opposite to the earlier
assumption based on the stale T3902 library metadata.

The on-board parts already provide 100 nF decoupling per microphone, 10 uF plus
100 nF on both sides of the LDO, separate ferrite beads in the microphone supply
branches, and 0-ohm DATA links. Keep the external `SCK` and `DATA` wires short,
route them next to ground, and keep them away from the PAM8403 class-D speaker
outputs.

The firmware target remains stereo PCM16 at 16 kHz on I2S0 PDM RX. GPIO10,
GPIO11, and GPIO12 are assigned to this board in `v7s-plus.yaml` and are not
ESP32-S3 boot strapping pins.

### Local microphone test without Home Assistant

The ESPHome firmware continuously publishes a two-channel level marker over
its native USB Serial/JTAG port. After flashing `v7s-plus.yaml`, leave the ESP
connected by USB and run `mic_level.bat` from the repository root. The console
tool automatically finds an Espressif USB port and shows physical
`LEFT/BM1` and `RIGHT/BM2` RMS bars. Internally BM1 arrives in PDM slot R and
BM2 arrives in PDM slot L; the utility reorders them for an intuitive display.
Stop it with `Ctrl+C`.

If automatic port selection is ambiguous, pass the port explicitly:

```bat
mic_level.bat COM21
```

No Home Assistant server, API client, or Wi-Fi connection is required. A valid
idle PDM microphone still produces a little varying noise. If the utility says
`ЛИНИЯ ПОСТОЯННАЯ`, the PCM stream is stuck at one raw value; check the board's
5 V rail, 3.3 V after the LDO, `EN`/GPIO12, `SCK`/GPIO10, and `DATA`/GPIO11.

The first isolation test showed a live `PDM R/BM1` slot and a nearly flat
`PDM L/BM2` slot. Removing `R1` made both slots flat. This proves that the
previously moving bar came from BM1; it does not mean that R1 feeds both
microphones. A subsequent raw-mode I2S capture with only BM2 connected proved
that BM2 does drive DATA: at the normal 1.024 MHz clock, 32,768 captured PDM bits
contained 50.89% ones and 69.66% transitions. Restore `R1` before continuing
normal stereo tests.

Six raw BM2 captures taken during speech/tapping still showed essentially fixed
local density (31-35 ones in every 64-bit window and 50.88-50.89% ones overall).
The digital PDM modulator is therefore alive, but no useful acoustic modulation
was observed. Inspect the BM2 bottom acoustic port and its alignment with the
PCB hole without inserting a probe into the port. If the opening is clear and
the package is correctly seated, replace BM2.

For the same raw test after reworking the board, flash `bm2-pdm-raw.yaml` and
run `capture_bm2_raw.bat COM21`. This records 4,096 bytes / 32,768 unfiltered
PDM bits in `bm2_raw_pdm_*.bin` and reports the one-bit density and transition
count. The diagnostic firmware and the normal PCM firmware both use the minimum
practical 1.024 MHz PDM clock. Return to the regular level meter by flashing
`v7s-plus.yaml` again and running `mic_level.bat`.

For BM2, verify approximately 3.3 V directly between pins 5 (`VDD`) and 3
(`GND`) while powered. With all power removed, check `L2`, continuity from BM2
pin 1 (`DATA`) to connector `XP1` pin 6, and the `R5` SELECT strap. BM2's DATA
path is direct and does not use R1. The raw capture already confirms digital
activity on that path; lack of acoustic response points to the capsule/port
rather than ESP32 PDM configuration.

Physical orientation from the assembly drawing: look at the component side with
connector `XP1` at the bottom. `BM1` is the upper-left microphone and `BM2` is
the upper-right microphone. `C1`/`L1` are below BM1 and `C2`/`L2` are below BM2.
`R1` and `R5` are the small links at the top between the microphones; `R2` is
the centre link below them. BM2 reaches the common DATA trace directly, while
only BM1 depends on R1.

## Current PCM5102A + PAM8403 playback path

The selected stereo playback hardware, jumpers, power distribution, and speaker
wiring are documented in
[audio/PCM5102A_PAM8403_WIRING.md](audio/PCM5102A_PAM8403_WIRING.md).

It keeps the reserved audio pins:

| Signal | ESP32-S3 | PCM5102A |
|---|---:|---|
| DAC soft mute | GPIO4 | `XSMT` |
| I2S BCLK | GPIO5 | `BCLK` |
| I2S LRCLK / WS | GPIO6 | `LCK` / `LRCK` |
| I2S data out | GPIO7 | `DIN` |
| Ground | GND | `GND` |

PCM5102A feeds the analog inputs of the PAM8403 stereo class-D amplifier. The
playback path uses I2S1 in master TX mode. I2S0 remains reserved for the stereo
PDM microphone input. The old slave connection to ESP32-WROOM stays removed.

Do not connect a bare speaker directly to the S3. Speaker power and wiring
are described in the module-specific document above.

## Echo cancellation and interruption

All robot speech and music must pass through the S3 audio pipeline. The exact
PCM sent to the MAX amplifier is also the far-end reference for ESP-SR AEC.
The microphone channels are the near-end input. Planned processing order:

`2x PDM mic -> ESP-SR AEC + NS -> clean mono PCM16/16 kHz -> Silero VAD -> STT`

Use `AEC_MODE_FD_LOW_COST` first. It is intended for full-duplex interaction:
the robot can play speech or music, remove that known playback from its
microphones, detect a person speaking over it, stop playback, and continue
capturing the person's phrase. Audio played outside this common pipeline has no
digital reference and cannot be cancelled reliably.
