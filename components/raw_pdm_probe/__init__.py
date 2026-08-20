import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID


raw_pdm_probe_ns = cg.esphome_ns.namespace("raw_pdm_probe")
RawPdmProbe = raw_pdm_probe_ns.class_("RawPdmProbe", cg.Component)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(RawPdmProbe),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
