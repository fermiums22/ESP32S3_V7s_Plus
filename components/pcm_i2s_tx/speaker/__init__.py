from pathlib import Path

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import audio, speaker
from esphome.const import CONF_ID
from esphome.core import CORE, HexInt, ID

AUTO_LOAD = ["audio"]
DEPENDENCIES = ["pcm_i2s_tx"]

ns = cg.esphome_ns.namespace("pcm_i2s_tx")
PcmI2sTx = ns.class_("PcmI2sTx", cg.Component, speaker.Speaker)
CONF_TEST_AUDIO = "test_audio"


def _set_stream_limits(config):
    audio.set_stream_limits(
        min_bits_per_sample=16,
        max_bits_per_sample=16,
        min_channels=2,
        max_channels=2,
        min_sample_rate=44100,
        max_sample_rate=44100,
    )(config)
    return config

CONFIG_SCHEMA = cv.All(
    speaker.SPEAKER_SCHEMA.extend(
        {
            cv.GenerateID(): cv.declare_id(PcmI2sTx),
            cv.Optional(CONF_TEST_AUDIO): cv.file_,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _set_stream_limits,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await speaker.register_speaker(var, config)
    if CONF_TEST_AUDIO in config:
        path: Path = CORE.relative_config_path(config[CONF_TEST_AUDIO])
        audio_data = path.read_bytes()
        data_id = ID(f"{config[CONF_ID]}_test_audio", is_declaration=True, type=cg.uint8)
        data = cg.progmem_array(data_id, [HexInt(value) for value in audio_data])
        cg.add(var.set_test_audio(data, len(audio_data)))
