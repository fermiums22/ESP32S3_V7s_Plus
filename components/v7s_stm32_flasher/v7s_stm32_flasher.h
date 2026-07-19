#pragma once

#include "esphome/core/component.h"
#include "esphome/core/gpio.h"
#include "esphome/components/button/button.h"
#include "esphome/components/modbus_controller/modbus_controller.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/uart/uart.h"

#include <cstdint>
#include <cstdio>
#include <string>

namespace esphome::v7s_stm32_flasher {

class FirmwareUploadHandler;

class STM32Flasher : public Component, public uart::UARTDevice {
 public:
  void setup() override;
  void dump_config() override;
  // pcm_i2s_tx mounts the audio SPIFFS at AFTER_WIFI. Use that existing mount.
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI - 1.0f; }

  void set_boot_pin(GPIOPin *pin) { this->boot_pin_ = pin; }
  void set_reset_pin(GPIOPin *pin) { this->reset_pin_ = pin; }
  void set_modbus_controller(modbus_controller::ModbusController *controller) { this->controller_ = controller; }
  void set_progress_sensor(sensor::Sensor *sensor) { this->progress_ = sensor; }
  void set_status_sensor(text_sensor::TextSensor *sensor) { this->status_ = sensor; }
  void set_upload_status_sensor(text_sensor::TextSensor *sensor) { this->upload_status_ = sensor; }
  void set_upload_size_sensor(sensor::Sensor *sensor) { this->upload_size_ = sensor; }
  void set_upload_token(const std::string &token) { this->upload_token_ = token; }

  // This action only accepts a validated image staged during the current S3
  // uptime. There is intentionally no embedded or persistent fallback.
  void flash();

 protected:
  friend class FirmwareUploadHandler;

  enum class FirmwareSource : uint8_t { NONE, STAGED };

  static constexpr uint32_t APP_BAUD = 921600;
  static constexpr uint32_t BOOT_BAUD = 230400;
  static constexpr uint32_t FLASH_BASE = 0x08000000;
  static constexpr uint32_t FLASH_END = FLASH_BASE + 64 * 1024;
  static constexpr uint8_t ACK = 0x79;
  static constexpr size_t MAX_FIRMWARE_SIZE = 64 * 1024;
  static constexpr size_t BLOCK_SIZE = 256;
  // OTA cannot add a partition. Use a single transient staging file in the
  // provisioned audio SPIFFS without touching /audio/Balensiaga.mp3.
  static constexpr const char *FS_BASE_PATH = "/audio";
  static constexpr const char *FS_PARTITION_LABEL = "audio";
  static constexpr const char *STAGING_PATH = "/audio/v7s_stm32.stage";

  bool mount_filesystem_();
  bool validate_staged_firmware_(size_t *size, uint32_t *crc) const;
  bool validate_firmware_file_(const char *path, size_t *size, uint32_t *crc) const;
  void invalidate_staging_();
  bool prepare_firmware_();
  bool read_firmware_block_(size_t offset, uint8_t *data, size_t size);
  void close_active_firmware_();
  void flash_();

  void begin_upload_(const std::string &filename, const std::string &token);
  void write_upload_(size_t index, const uint8_t *data, size_t size);
  void finish_upload_();
  void abort_upload_();
  bool upload_authorized_(const std::string &token) const;
  void set_upload_result_(int http_status, const std::string &message);
  void publish_upload_status_(const std::string &message);
  static uint32_t crc32_update_(uint32_t crc, const uint8_t *data, size_t size);
  static uint32_t read_le32_(const uint8_t *data);

  bool enter_bootloader_();
  bool erase_();
  bool write_firmware_();
  bool verify_firmware_();
  bool command_(uint8_t command, uint32_t timeout_ms = 1000);
  bool send_address_(uint32_t address);
  bool wait_ack_(uint32_t timeout_ms);
  bool read_exact_(uint8_t *data, size_t size, uint32_t timeout_ms);
  void drain_uart_();
  void set_uart_(uint32_t baud, uart::UARTParityOptions parity);
  void reset_target_(bool bootloader);
  void finish_(bool success, const char *status);
  void publish_(const char *status, float progress);
  void stop_robot_();

  GPIOPin *boot_pin_{nullptr};
  GPIOPin *reset_pin_{nullptr};
  modbus_controller::ModbusController *controller_{nullptr};
  sensor::Sensor *progress_{nullptr};
  text_sensor::TextSensor *status_{nullptr};
  text_sensor::TextSensor *upload_status_{nullptr};
  sensor::Sensor *upload_size_{nullptr};
  std::string upload_token_;
  std::string upload_response_;
  int upload_http_status_{503};
  FILE *upload_file_{nullptr};
  FILE *active_file_{nullptr};
  size_t upload_bytes_{0};
  uint32_t upload_crc_{0xFFFFFFFFU};
  size_t staged_firmware_size_{0};
  uint32_t staged_firmware_crc_{0};
  size_t active_firmware_size_{0};
  FirmwareSource active_source_{FirmwareSource::NONE};
  bool filesystem_mounted_{false};
  bool staging_valid_{false};
  bool upload_failed_{false};
  bool running_{false};
};

class STM32FlashButton : public button::Button, public Parented<STM32Flasher> {
 protected:
  void press_action() override { this->parent_->flash(); }
};

}  // namespace esphome::v7s_stm32_flasher
