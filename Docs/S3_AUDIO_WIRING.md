# ESP32-S3 audio wiring

## Two TDK T3902 PDM microphones

Reserved pins on `v7s-plus`:

| Signal | ESP32-S3 | T3902 |
|---|---:|---|
| PDM clock | GPIO10 | pin 4 `CLK` of both microphones |
| PDM stereo data | GPIO11 | pin 1 `DATA` of both microphones |
| Supply | 3V3 | pin 5 `VDD` of both microphones |
| Ground | GND | pin 3 `GND` of both microphones |

The two microphones share one clock and one physical data line. Their channel
is selected at the microphone:

| Microphone | pin 2 `SELECT` | PDM channel |
|---|---|---|
| Left | 3V3, same rail as `VDD` | left / DATA2 |
| Right | GND | right / DATA1 |

On the shown schematic, fit only one SELECT strap for each microphone. Never
fit the 0-ohm pull-up and 0-ohm pull-down simultaneously: that shorts 3V3 to
GND. Join `SD_MIC_1` and `SD_MIC_2` after their series resistors and route the
joined signal to GPIO11.

Fit one 100 nF X7R decoupling capacitor directly between pins 5 and 3 of each
microphone. Add 4.7 uF near the microphone pair. Use 22-33 ohm in series with
PDM CLK near the S3; 22-47 ohm in each DATA branch is acceptable. Keep CLK and
DATA short, route them over ground, and keep them away from the class-D speaker
outputs. The acoustic port is on the bottom: provide a PCB hole and copper,
solder-mask, and enclosure keepout under it.

Initial firmware target is stereo PCM16 at 16 kHz on I2S0 PDM RX. A 2.048 MHz
PDM clock keeps the T3902 in standard mode. GPIO10 and GPIO11 are currently
unused by the robot firmware and are not ESP32-S3 boot strapping pins.

## Future MAX98357-family I2S amplifier

Keep the existing audio pins:

| Signal | ESP32-S3 | MAX amplifier |
|---|---:|---|
| I2S BCLK | GPIO5 | `BCLK` |
| I2S LRCLK / WS | GPIO6 | `LRC` / `WS` |
| I2S data out | GPIO7 | `DIN` |
| Optional shutdown | GPIO4 | `SD` / `EN` |
| Ground | GND | `GND` |

The amplifier uses I2S1 in master TX mode. I2S0 is reserved for the T3902
PDM-to-PCM hardware converter. The old slave connection to the ESP32-WROOM is
removed and GPIO5/GPIO6/GPIO7 must no longer be wired to that controller.

Do not connect a bare speaker directly to the S3. Speaker power and wiring
depend on the exact MAX module; confirm its part number before connecting VIN,
gain, SD, and the speaker.

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
