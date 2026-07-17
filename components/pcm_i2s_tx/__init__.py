import esphome.config_validation as cv
from esphome.components.esp32 import include_builtin_idf_component

CODEOWNERS = []
CONFIG_SCHEMA = cv.Schema({})


async def to_code(config):
    include_builtin_idf_component("spiffs")
