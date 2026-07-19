import esphome.codegen as cg
from esphome import pins
from esphome.components import button, modbus_controller, sensor, text_sensor, uart
import esphome.config_validation as cv
from esphome.const import (
    CONF_ID,
    ENTITY_CATEGORY_CONFIG,
    ENTITY_CATEGORY_DIAGNOSTIC,
)

CODEOWNERS = []
DEPENDENCIES = ["uart", "modbus_controller", "web_server"]
AUTO_LOAD = ["button", "sensor", "text_sensor", "web_server_idf"]

CONF_BOOT_PIN = "boot_pin"
CONF_RESET_PIN = "reset_pin"
CONF_FLASH_BUTTON = "flash_button"
CONF_MODBUS_CONTROLLER_ID = "modbus_controller_id"
CONF_PROGRESS = "progress"
CONF_STATUS = "status"
CONF_UPLOAD_TOKEN = "upload_token"
CONF_UPLOAD_STATUS = "upload_status"
CONF_UPLOAD_SIZE = "upload_size"

ns = cg.esphome_ns.namespace("v7s_stm32_flasher")
STM32Flasher = ns.class_("STM32Flasher", cg.Component, uart.UARTDevice)
STM32FlashButton = ns.class_("STM32FlashButton", button.Button, cg.Parented.template(STM32Flasher))


CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(STM32Flasher),
            cv.GenerateID(CONF_MODBUS_CONTROLLER_ID): cv.use_id(
                modbus_controller.ModbusController
            ),
            cv.Required(CONF_BOOT_PIN): pins.gpio_output_pin_schema,
            cv.Required(CONF_RESET_PIN): pins.gpio_output_pin_schema,
            cv.Required(CONF_FLASH_BUTTON): button.button_schema(
                STM32FlashButton,
                entity_category=ENTITY_CATEGORY_CONFIG,
                icon="mdi:chip",
            ),
            cv.Required(CONF_PROGRESS): sensor.sensor_schema(
                unit_of_measurement="%",
                accuracy_decimals=0,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:progress-upload",
            ),
            cv.Required(CONF_STATUS): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:chip",
            ),
            cv.Required(CONF_UPLOAD_TOKEN): cv.string_strict,
            cv.Required(CONF_UPLOAD_STATUS): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:database-check-outline",
            ),
            cv.Required(CONF_UPLOAD_SIZE): sensor.sensor_schema(
                unit_of_measurement="B",
                accuracy_decimals=0,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:database-outline",
            ),
        }
    ).extend(cv.COMPONENT_SCHEMA).extend(uart.UART_DEVICE_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)

    controller = await cg.get_variable(config[CONF_MODBUS_CONTROLLER_ID])
    cg.add(var.set_modbus_controller(controller))
    boot_pin = await cg.gpio_pin_expression(config[CONF_BOOT_PIN])
    reset_pin = await cg.gpio_pin_expression(config[CONF_RESET_PIN])
    cg.add(var.set_boot_pin(boot_pin))
    cg.add(var.set_reset_pin(reset_pin))

    flash_button = await button.new_button(config[CONF_FLASH_BUTTON])
    await cg.register_parented(flash_button, var)
    progress = await sensor.new_sensor(config[CONF_PROGRESS])
    status = await text_sensor.new_text_sensor(config[CONF_STATUS])
    cg.add(var.set_progress_sensor(progress))
    cg.add(var.set_status_sensor(status))
    cg.add(var.set_upload_token(config[CONF_UPLOAD_TOKEN]))
    upload_status = await text_sensor.new_text_sensor(config[CONF_UPLOAD_STATUS])
    cg.add(var.set_upload_status_sensor(upload_status))
    upload_size = await sensor.new_sensor(config[CONF_UPLOAD_SIZE])
    cg.add(var.set_upload_size_sensor(upload_size))
