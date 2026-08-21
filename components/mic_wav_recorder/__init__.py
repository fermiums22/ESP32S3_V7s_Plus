import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import microphone
from esphome.const import CONF_ID, CONF_MICROPHONE

CODEOWNERS = []
DEPENDENCIES = ["microphone", "psram"]

mic_wav_recorder_ns = cg.esphome_ns.namespace("mic_wav_recorder")
MicWavRecorder = mic_wav_recorder_ns.class_("MicWavRecorder", cg.Component)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(MicWavRecorder),
        cv.Required(CONF_MICROPHONE): cv.use_id(microphone.Microphone),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    mic = await cg.get_variable(config[CONF_MICROPHONE])
    cg.add(var.set_microphone(mic))
