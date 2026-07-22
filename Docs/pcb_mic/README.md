# Stereo PDM microphone board

Altium sources and fabrication outputs for the robot's two TDK T3902 PDM
microphones. Electrical interface and S3 pin assignment are documented in
[`../S3_AUDIO_WIRING.md`](../S3_AUDIO_WIRING.md).

## Review status

The design is a work in progress and must not be fabricated until the input
capacitor issue below is corrected and ERC/DRC are rerun.

- `TPS7A9001DSKR`, R1 = 4.7 kOhm and R2 = 1.5 kOhm produce approximately
  3.31 V; the feedback divider is correct.
- C4 = 10 uF satisfies the LDO output-capacitance requirement.
- Replace C3 = 100 nF with 10 uF, or add 10 uF in parallel directly at the
  LDO IN pins. TPS7A90 requires 10 uF on its input.
- Populate the PDM DATA branch resistors with 22-33 Ohm instead of 0 Ohm if
  ringing is observed. Add a 22-33 Ohm series resistor to SCK near the source.
- Verify both 0.6 mm bottom-port holes align with the microphone acoustic
  ports and have copper/solder-mask keepouts.
- The microphone spacing is suitable for stereo capture/AEC experiments but
  is small for reliable sound-direction estimation. Use a wider mechanical
  baseline if localization is required.

Before assembly, run Altium ERC/DRC and inspect the generated Gerbers and drill
files. The current drill report contains six 1.0 mm plated connector holes and
two 0.6 mm non-plated acoustic holes.
