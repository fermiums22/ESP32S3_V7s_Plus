import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import microphone
from esphome.const import CONF_ID, CONF_MICROPHONE

CODEOWNERS = []
DEPENDENCIES = ["microphone"]

mic_level_meter_ns = cg.esphome_ns.namespace("mic_level_meter")
MicLevelMeter = mic_level_meter_ns.class_("MicLevelMeter", cg.PollingComponent)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(MicLevelMeter),
        cv.Required(CONF_MICROPHONE): cv.use_id(microphone.Microphone),
    }
).extend(cv.polling_component_schema("100ms"))


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    mic = await cg.get_variable(config[CONF_MICROPHONE])
    cg.add(var.set_microphone(mic))
