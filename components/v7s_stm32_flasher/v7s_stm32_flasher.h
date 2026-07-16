#pragma once

#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/gpio.h"
#include "esphome/components/button/button.h"
#include "esphome/components/modbus_controller/modbus_controller.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/uart/uart.h"

namespace esphome::v7s_stm32_flasher {

class STM32Flasher : public Component, public uart::UARTDevice {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_boot_pin(GPIOPin *pin) { this->boot_pin_ = pin; }
  void set_reset_pin(GPIOPin *pin) { this->reset_pin_ = pin; }
  void set_modbus_controller(modbus_controller::ModbusController *controller) { this->controller_ = controller; }
  void set_firmware(const uint8_t *data, size_t size) { this->firmware_ = data; this->firmware_size_ = size; }
  void set_progress_sensor(sensor::Sensor *sensor) { this->progress_ = sensor; }
  void set_status_sensor(text_sensor::TextSensor *sensor) { this->status_ = sensor; }

  void flash();

 protected:
  static constexpr uint32_t APP_BAUD = 921600;
  static constexpr uint32_t BOOT_BAUD = 230400;
  static constexpr uint32_t FLASH_BASE = 0x08000000;
  static constexpr uint8_t ACK = 0x79;
  static constexpr uint8_t NACK = 0x1F;
  static constexpr size_t BLOCK_SIZE = 256;

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
  const uint8_t *firmware_{nullptr};
  size_t firmware_size_{0};
  bool running_{false};
};

class STM32FlashButton : public button::Button, public Parented<STM32Flasher> {
 protected:
  void press_action() override { this->parent_->flash(); }
};

}  // namespace esphome::v7s_stm32_flasher
