#pragma once

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/button/button.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/switch/switch.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/component.h"

#include <array>

namespace esphome::gopro_ble {

class GoProRecordingSwitch;
class GoProLocateSwitch;

class GoProBLE : public Component, public ble_client::BLEClientNode {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }
  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;

  void set_connected_sensor(binary_sensor::BinarySensor *value) { this->connected_sensor_ = value; }
  void set_battery_sensor(sensor::Sensor *value) { this->battery_sensor_ = value; }
  void set_status_sensor(text_sensor::TextSensor *value) { this->status_sensor_ = value; }
  void set_recording_switch(GoProRecordingSwitch *value) { this->recording_switch_ = value; }
  void set_locate_switch(GoProLocateSwitch *value) { this->locate_switch_ = value; }

  void set_recording(bool value);
  void set_locate(bool value);
  void sleep_camera();
  void refresh();

 protected:
  enum class Action : uint8_t {
    PAIR_COMPLETE,
    SHUTTER_ON,
    SHUTTER_OFF,
    LOCATE_ON,
    LOCATE_OFF,
    SLEEP,
    QUERY_BATTERY,
    QUERY_ENCODING,
    QUERY_BUSY,
    QUERY_OVERHEATING,
    QUERY_LOCATE,
  };

  static constexpr size_t ACTION_QUEUE_SIZE = 12;
  static constexpr size_t RX_BUFFER_SIZE = 512;

  bool enqueue_(Action action);
  bool send_action_(Action action);
  bool write_payload_(uint16_t handle, const uint8_t *payload, size_t size);
  void discover_characteristics_();
  void handle_notification_(uint16_t handle, const uint8_t *data, size_t size);
  bool accumulate_(const uint8_t *data, size_t size);
  void parse_query_response_();
  void publish_status_(const char *status);
  void set_ready_(bool ready);

  binary_sensor::BinarySensor *connected_sensor_{nullptr};
  sensor::Sensor *battery_sensor_{nullptr};
  text_sensor::TextSensor *status_sensor_{nullptr};
  GoProRecordingSwitch *recording_switch_{nullptr};
  GoProLocateSwitch *locate_switch_{nullptr};

  uint16_t command_handle_{0};
  uint16_t command_response_handle_{0};
  uint16_t settings_handle_{0};
  uint16_t settings_response_handle_{0};
  uint16_t query_handle_{0};
  uint16_t query_response_handle_{0};
  uint16_t network_handle_{0};
  uint16_t network_response_handle_{0};
  uint8_t notify_mask_{0};

  std::array<Action, ACTION_QUEUE_SIZE> actions_{};
  uint8_t action_head_{0};
  uint8_t action_tail_{0};
  bool write_pending_{false};
  bool ready_{false};
  bool pair_enqueued_{false};
  uint32_t last_write_ms_{0};
  uint32_t last_keep_alive_ms_{0};
  uint32_t last_poll_ms_{0};
  uint8_t poll_index_{0};

  std::array<uint8_t, RX_BUFFER_SIZE> rx_{};
  size_t rx_size_{0};
  size_t rx_expected_{0};
};

class GoProRecordingSwitch : public switch_::Switch, public Parented<GoProBLE> {
 protected:
  void write_state(bool state) override { this->parent_->set_recording(state); }
};

class GoProLocateSwitch : public switch_::Switch, public Parented<GoProBLE> {
 protected:
  void write_state(bool state) override { this->parent_->set_locate(state); }
};

class GoProSleepButton : public button::Button, public Parented<GoProBLE> {
 protected:
  void press_action() override { this->parent_->sleep_camera(); }
};

class GoProRefreshButton : public button::Button, public Parented<GoProBLE> {
 protected:
  void press_action() override { this->parent_->refresh(); }
};

}  // namespace esphome::gopro_ble
