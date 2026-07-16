import esphome.codegen as cg
from esphome.components import binary_sensor, ble_client, button, sensor, switch, text_sensor
import esphome.config_validation as cv
from esphome.const import (
    CONF_ID,
    DEVICE_CLASS_BATTERY,
    DEVICE_CLASS_CONNECTIVITY,
    ENTITY_CATEGORY_DIAGNOSTIC,
    UNIT_PERCENT,
)

CODEOWNERS = []
DEPENDENCIES = ["ble_client"]
AUTO_LOAD = ["binary_sensor", "button", "sensor", "switch", "text_sensor"]

CONF_CONNECTED = "connected"
CONF_RECORDING = "recording"
CONF_BATTERY = "battery"
CONF_LOCATE = "locate"
CONF_KEEP_AWAKE = "keep_awake"
CONF_SLEEP = "sleep"
CONF_WAKE = "wake"
CONF_REFRESH = "refresh"
CONF_STATUS = "status"
CONF_IDLE_TIMEOUT = "idle_timeout"

ns = cg.esphome_ns.namespace("gopro_ble")
GoProBLE = ns.class_("GoProBLE", cg.Component, ble_client.BLEClientNode)
GoProRecordingSwitch = ns.class_(
    "GoProRecordingSwitch", switch.Switch, cg.Parented.template(GoProBLE)
)
GoProLocateSwitch = ns.class_(
    "GoProLocateSwitch", switch.Switch, cg.Parented.template(GoProBLE)
)
GoProKeepAwakeSwitch = ns.class_(
    "GoProKeepAwakeSwitch", switch.Switch, cg.Parented.template(GoProBLE)
)
GoProSleepButton = ns.class_(
    "GoProSleepButton", button.Button, cg.Parented.template(GoProBLE)
)
GoProRefreshButton = ns.class_(
    "GoProRefreshButton", button.Button, cg.Parented.template(GoProBLE)
)
GoProWakeButton = ns.class_(
    "GoProWakeButton", button.Button, cg.Parented.template(GoProBLE)
)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(GoProBLE),
            cv.Required(CONF_CONNECTED): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_CONNECTIVITY,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Required(CONF_RECORDING): switch.switch_schema(GoProRecordingSwitch),
            cv.Required(CONF_LOCATE): switch.switch_schema(GoProLocateSwitch),
            cv.Required(CONF_KEEP_AWAKE): switch.switch_schema(GoProKeepAwakeSwitch),
            cv.Required(CONF_BATTERY): sensor.sensor_schema(
                unit_of_measurement=UNIT_PERCENT,
                accuracy_decimals=0,
                device_class=DEVICE_CLASS_BATTERY,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Required(CONF_SLEEP): button.button_schema(
                GoProSleepButton, icon="mdi:power-sleep"
            ),
            cv.Required(CONF_WAKE): button.button_schema(
                GoProWakeButton, icon="mdi:power"
            ),
            cv.Required(CONF_REFRESH): button.button_schema(
                GoProRefreshButton,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:refresh",
            ),
            cv.Required(CONF_STATUS): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:camera-wireless",
            ),
            cv.Optional(CONF_IDLE_TIMEOUT, default="5min"): cv.positive_time_period_milliseconds,
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(ble_client.BLE_CLIENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await ble_client.register_ble_node(var, config)

    connected = await binary_sensor.new_binary_sensor(config[CONF_CONNECTED])
    battery = await sensor.new_sensor(config[CONF_BATTERY])
    status = await text_sensor.new_text_sensor(config[CONF_STATUS])
    cg.add(var.set_connected_sensor(connected))
    cg.add(var.set_battery_sensor(battery))
    cg.add(var.set_status_sensor(status))
    cg.add(var.set_idle_timeout(config[CONF_IDLE_TIMEOUT].total_milliseconds))

    recording = await switch.new_switch(config[CONF_RECORDING])
    locate = await switch.new_switch(config[CONF_LOCATE])
    keep_awake = await switch.new_switch(config[CONF_KEEP_AWAKE])
    sleep = await button.new_button(config[CONF_SLEEP])
    wake = await button.new_button(config[CONF_WAKE])
    refresh = await button.new_button(config[CONF_REFRESH])
    await cg.register_parented(recording, var)
    await cg.register_parented(locate, var)
    await cg.register_parented(keep_awake, var)
    await cg.register_parented(sleep, var)
    await cg.register_parented(wake, var)
    await cg.register_parented(refresh, var)
    cg.add(var.set_recording_switch(recording))
    cg.add(var.set_locate_switch(locate))
