import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import microphone
from esphome.const import CONF_ID, CONF_MICROPHONE

CONF_HOST = "host"
CONF_PORT = "port"

CODEOWNERS = []
AUTO_LOAD = ["socket"]
DEPENDENCIES = ["microphone", "network"]

wifi_pcm_streamer_ns = cg.esphome_ns.namespace("wifi_pcm_streamer")
WifiPcmStreamer = wifi_pcm_streamer_ns.class_("WifiPcmStreamer", cg.Component)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(WifiPcmStreamer),
        cv.Required(CONF_MICROPHONE): cv.use_id(microphone.Microphone),
        cv.Required(CONF_HOST): cv.string_strict,
        cv.Optional(CONF_PORT, default=10300): cv.port,
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    mic = await cg.get_variable(config[CONF_MICROPHONE])
    cg.add(var.set_microphone(mic))
    cg.add(var.set_host(config[CONF_HOST]))
    cg.add(var.set_port(config[CONF_PORT]))
