#include "gopro_ble.h"

#include "esphome/components/esp32_ble/ble_uuid.h"
#include "esphome/core/log.h"

#include <cmath>
#include <cstring>

namespace esphome::gopro_ble {

namespace espbt = esphome::esp32_ble_tracker;

static const char *const TAG = "gopro_ble";
static const char *const GOPRO_UUID_PREFIX = "b5f9";
static const char *const GOPRO_UUID_SUFFIX = "-aa8d-11e3-9046-0002a5d5c51b";

static espbt::ESPBTUUID gopro_uuid(uint16_t short_uuid) {
  char value[37];
  snprintf(value, sizeof(value), "%s%04x%s", GOPRO_UUID_PREFIX, short_uuid, GOPRO_UUID_SUFFIX);
  return espbt::ESPBTUUID::from_raw(value);
}

void GoProBLE::setup() {
  this->set_ready_(false);
  this->publish_status_("BLE disconnected");
  if (this->battery_sensor_ != nullptr)
    this->battery_sensor_->publish_state(NAN);
}

void GoProBLE::dump_config() {
  ESP_LOGCONFIG(TAG, "GoPro Open GoPro BLE controller:");
  ESP_LOGCONFIG(TAG, "  Camera: %s", this->parent()->address_str());
  LOG_BINARY_SENSOR("  ", "Connected", this->connected_sensor_);
  LOG_SENSOR("  ", "Battery", this->battery_sensor_);
  LOG_SWITCH("  ", "Recording", this->recording_switch_);
  LOG_SWITCH("  ", "Locate", this->locate_switch_);
}

void GoProBLE::publish_status_(const char *status) {
  ESP_LOGI(TAG, "%s", status);
  if (this->status_sensor_ != nullptr)
    this->status_sensor_->publish_state(status);
}

void GoProBLE::set_ready_(bool ready) {
  this->ready_ = ready;
  if (this->connected_sensor_ != nullptr)
    this->connected_sensor_->publish_state(ready);
  if (!ready) {
    this->write_pending_ = false;
    this->action_head_ = this->action_tail_ = 0;
    this->notify_mask_ = 0;
    this->pair_enqueued_ = false;
  }
}

bool GoProBLE::enqueue_(Action action) {
  const uint8_t next = (this->action_head_ + 1U) % ACTION_QUEUE_SIZE;
  if (next == this->action_tail_) {
    ESP_LOGW(TAG, "Command queue full");
    return false;
  }
  this->actions_[this->action_head_] = action;
  this->action_head_ = next;
  return true;
}

void GoProBLE::set_recording(bool value) {
  if (!this->ready_) {
    this->publish_status_("GoPro BLE unavailable");
    return;
  }
  this->enqueue_(value ? Action::SHUTTER_ON : Action::SHUTTER_OFF);
  this->enqueue_(Action::QUERY_ENCODING);
}

void GoProBLE::set_locate(bool value) {
  if (!this->ready_) {
    this->publish_status_("GoPro BLE unavailable");
    return;
  }
  this->enqueue_(value ? Action::LOCATE_ON : Action::LOCATE_OFF);
  this->enqueue_(Action::QUERY_LOCATE);
}

void GoProBLE::sleep_camera() {
  if (this->ready_)
    this->enqueue_(Action::SLEEP);
}

void GoProBLE::refresh() {
  if (!this->ready_)
    return;
  this->enqueue_(Action::QUERY_BATTERY);
  this->enqueue_(Action::QUERY_ENCODING);
  this->enqueue_(Action::QUERY_BUSY);
  this->enqueue_(Action::QUERY_OVERHEATING);
  this->enqueue_(Action::QUERY_LOCATE);
}

bool GoProBLE::write_payload_(uint16_t handle, const uint8_t *payload, size_t size) {
  if (!this->ready_ || handle == 0 || payload == nullptr || size == 0 || size > 18)
    return false;
  uint8_t frame[20];
  frame[0] = 0x20;  // Open GoPro extended 13-bit packet header.
  frame[1] = static_cast<uint8_t>(size);
  memcpy(&frame[2], payload, size);
  const esp_err_t result = esp_ble_gattc_write_char(
      this->parent()->get_gattc_if(), this->parent()->get_conn_id(), handle, size + 2, frame,
      ESP_GATT_WRITE_TYPE_RSP, ESP_GATT_AUTH_REQ_NONE);
  if (result != ESP_OK) {
    ESP_LOGW(TAG, "BLE write failed: %s", esp_err_to_name(result));
    return false;
  }
  this->write_pending_ = true;
  this->last_write_ms_ = millis();
  return true;
}

bool GoProBLE::send_action_(Action action) {
  uint8_t payload[18];
  size_t size = 0;
  uint16_t handle = this->command_handle_;
  switch (action) {
    case Action::PAIR_COMPLETE: {
      static const uint8_t pair[] = {
          0x03, 0x01, 0x08, 0x00, 0x12, 0x09, 'E', 'S', 'P', '3', '2', ' ', 'V', '7', 's'};
      memcpy(payload, pair, sizeof(pair));
      size = sizeof(pair);
      handle = this->network_handle_;
      break;
    }
    case Action::SHUTTER_ON:
    case Action::SHUTTER_OFF:
      payload[0] = 0x01;
      payload[1] = 0x01;
      payload[2] = action == Action::SHUTTER_ON ? 1 : 0;
      size = 3;
      this->publish_status_(action == Action::SHUTTER_ON ? "starting recording" : "stopping recording");
      break;
    case Action::LOCATE_ON:
    case Action::LOCATE_OFF:
      payload[0] = 0x16;
      payload[1] = 0x01;
      payload[2] = action == Action::LOCATE_ON ? 1 : 0;
      size = 3;
      break;
    case Action::SLEEP:
      payload[0] = 0x05;
      size = 1;
      this->publish_status_("putting GoPro to sleep");
      break;
    case Action::QUERY_BATTERY:
      payload[0] = 0x13;
      payload[1] = 70;
      size = 2;
      handle = this->query_handle_;
      break;
    case Action::QUERY_ENCODING:
      payload[0] = 0x13;
      payload[1] = 10;
      size = 2;
      handle = this->query_handle_;
      break;
    case Action::QUERY_BUSY:
      payload[0] = 0x13;
      payload[1] = 8;
      size = 2;
      handle = this->query_handle_;
      break;
    case Action::QUERY_OVERHEATING:
      payload[0] = 0x13;
      payload[1] = 6;
      size = 2;
      handle = this->query_handle_;
      break;
    case Action::QUERY_LOCATE:
      payload[0] = 0x13;
      payload[1] = 45;
      size = 2;
      handle = this->query_handle_;
      break;
  }
  return this->write_payload_(handle, payload, size);
}

void GoProBLE::loop() {
  if (!this->ready_)
    return;
  const uint32_t now = millis();
  if (this->write_pending_ && now - this->last_write_ms_ > 2000) {
    ESP_LOGW(TAG, "BLE write confirmation timeout");
    this->write_pending_ = false;
  }
  if (this->write_pending_)
    return;

  if (this->action_tail_ != this->action_head_) {
    const Action action = this->actions_[this->action_tail_];
    if (this->send_action_(action))
      this->action_tail_ = (this->action_tail_ + 1U) % ACTION_QUEUE_SIZE;
    return;
  }

  if (now - this->last_keep_alive_ms_ >= 3000) {
    const uint8_t keep_alive[] = {91, 1, 0x42};
    if (this->write_payload_(this->settings_handle_, keep_alive, sizeof(keep_alive)))
      this->last_keep_alive_ms_ = now;
    return;
  }

  if (now - this->last_poll_ms_ >= 5000) {
    static const Action polls[] = {Action::QUERY_BATTERY, Action::QUERY_ENCODING, Action::QUERY_BUSY,
                                   Action::QUERY_OVERHEATING, Action::QUERY_LOCATE};
    this->enqueue_(polls[this->poll_index_++ % 5]);
    this->last_poll_ms_ = now;
  }
}

void GoProBLE::discover_characteristics_() {
  const auto service = espbt::ESPBTUUID::from_uint16(0xFEA6);
  auto find_handle = [&](uint16_t uuid) -> uint16_t {
    auto *characteristic = this->parent()->get_characteristic(service, gopro_uuid(uuid));
    return characteristic == nullptr ? 0 : characteristic->handle;
  };
  this->command_handle_ = find_handle(0x0072);
  this->command_response_handle_ = find_handle(0x0073);
  this->settings_handle_ = find_handle(0x0074);
  this->settings_response_handle_ = find_handle(0x0075);
  this->query_handle_ = find_handle(0x0076);
  this->query_response_handle_ = find_handle(0x0077);

  const auto network_service = gopro_uuid(0x0090);
  auto *network = this->parent()->get_characteristic(network_service, gopro_uuid(0x0091));
  auto *network_response = this->parent()->get_characteristic(network_service, gopro_uuid(0x0092));
  this->network_handle_ = network == nullptr ? 0 : network->handle;
  this->network_response_handle_ = network_response == nullptr ? 0 : network_response->handle;

  if (this->command_handle_ == 0 || this->command_response_handle_ == 0 || this->settings_handle_ == 0 ||
      this->settings_response_handle_ == 0 || this->query_handle_ == 0 || this->query_response_handle_ == 0) {
    this->publish_status_("Open GoPro characteristics missing");
    this->status_set_error();
    return;
  }

  esp_ble_gattc_register_for_notify(this->parent()->get_gattc_if(), this->parent()->get_remote_bda(),
                                    this->command_response_handle_);
  esp_ble_gattc_register_for_notify(this->parent()->get_gattc_if(), this->parent()->get_remote_bda(),
                                    this->settings_response_handle_);
  esp_ble_gattc_register_for_notify(this->parent()->get_gattc_if(), this->parent()->get_remote_bda(),
                                    this->query_response_handle_);
  if (this->network_response_handle_ != 0)
    esp_ble_gattc_register_for_notify(this->parent()->get_gattc_if(), this->parent()->get_remote_bda(),
                                      this->network_response_handle_);
}

bool GoProBLE::accumulate_(const uint8_t *data, size_t size) {
  if (data == nullptr || size == 0)
    return false;
  size_t offset = 0;
  if ((data[0] & 0x80U) != 0) {
    offset = 1;
  } else {
    this->rx_size_ = 0;
    const uint8_t header = (data[0] >> 5) & 0x03U;
    if (header == 0) {
      this->rx_expected_ = data[0] & 0x1FU;
      offset = 1;
    } else if (header == 1 && size >= 2) {
      this->rx_expected_ = ((data[0] & 0x1FU) << 8) | data[1];
      offset = 2;
    } else if (header == 2 && size >= 3) {
      this->rx_expected_ = (static_cast<size_t>(data[1]) << 8) | data[2];
      offset = 3;
    } else {
      return false;
    }
  }
  if (offset > size || this->rx_size_ + size - offset > this->rx_.size()) {
    this->rx_size_ = this->rx_expected_ = 0;
    return false;
  }
  memcpy(&this->rx_[this->rx_size_], &data[offset], size - offset);
  this->rx_size_ += size - offset;
  return this->rx_expected_ != 0 && this->rx_size_ == this->rx_expected_;
}

void GoProBLE::parse_query_response_() {
  if (this->rx_size_ < 2)
    return;
  const uint8_t command = this->rx_[0];
  const uint8_t result = this->rx_[1];
  if (result != 0) {
    ESP_LOGW(TAG, "GoPro query 0x%02X failed: %u", command, result);
    return;
  }
  size_t offset = 2;
  while (offset + 2 <= this->rx_size_) {
    const uint8_t id = this->rx_[offset++];
    const uint8_t length = this->rx_[offset++];
    if (offset + length > this->rx_size_)
      break;
    if (length != 0) {
      const uint8_t value = this->rx_[offset];
      if (id == 70 && this->battery_sensor_ != nullptr) {
        this->battery_sensor_->publish_state(value);
      } else if (id == 10 && this->recording_switch_ != nullptr) {
        this->recording_switch_->publish_state(value != 0);
        this->publish_status_(value != 0 ? "recording" : "ready");
      } else if (id == 8 && value != 0) {
        this->publish_status_("GoPro busy");
      } else if (id == 6 && value != 0) {
        this->publish_status_("GoPro overheating");
      } else if (id == 45 && this->locate_switch_ != nullptr) {
        this->locate_switch_->publish_state(value != 0);
      }
    }
    offset += length;
  }
}

void GoProBLE::handle_notification_(uint16_t handle, const uint8_t *data, size_t size) {
  if (handle == this->query_response_handle_ && this->accumulate_(data, size))
    this->parse_query_response_();
}

void GoProBLE::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                                   esp_ble_gattc_cb_param_t *param) {
  switch (event) {
    case ESP_GATTC_OPEN_EVT:
      if (param->open.status == ESP_GATT_OK)
        this->publish_status_("BLE connected, discovering");
      break;
    case ESP_GATTC_CLOSE_EVT:
      this->set_ready_(false);
      this->publish_status_("BLE disconnected");
      if (this->recording_switch_ != nullptr)
        this->recording_switch_->publish_state(false);
      if (this->locate_switch_ != nullptr)
        this->locate_switch_->publish_state(false);
      if (this->battery_sensor_ != nullptr)
        this->battery_sensor_->publish_state(NAN);
      break;
    case ESP_GATTC_SEARCH_CMPL_EVT:
      this->discover_characteristics_();
      break;
    case ESP_GATTC_REG_FOR_NOTIFY_EVT: {
      if (param->reg_for_notify.status != ESP_GATT_OK)
        break;
      if (param->reg_for_notify.handle == this->command_response_handle_)
        this->notify_mask_ |= 0x01;
      else if (param->reg_for_notify.handle == this->settings_response_handle_)
        this->notify_mask_ |= 0x02;
      else if (param->reg_for_notify.handle == this->query_response_handle_)
        this->notify_mask_ |= 0x04;
      else if (param->reg_for_notify.handle == this->network_response_handle_)
        this->notify_mask_ |= 0x08;
      const uint8_t required_mask = this->network_response_handle_ != 0 ? 0x0F : 0x07;
      if ((this->notify_mask_ & required_mask) == required_mask && !this->ready_) {
        this->node_state = espbt::ClientState::ESTABLISHED;
        this->set_ready_(true);
        this->status_clear_error();
        this->publish_status_("ready");
        this->last_keep_alive_ms_ = millis();
        if (this->network_handle_ != 0 && !this->pair_enqueued_) {
          this->enqueue_(Action::PAIR_COMPLETE);
          this->pair_enqueued_ = true;
        }
        this->refresh();
      }
      break;
    }
    case ESP_GATTC_WRITE_CHAR_EVT:
      this->write_pending_ = false;
      if (param->write.status != ESP_GATT_OK) {
        ESP_LOGW(TAG, "GoPro characteristic write error: %u", param->write.status);
        this->publish_status_("BLE write error");
      }
      break;
    case ESP_GATTC_NOTIFY_EVT:
      this->handle_notification_(param->notify.handle, param->notify.value, param->notify.value_len);
      break;
    default:
      break;
  }
}

}  // namespace esphome::gopro_ble
