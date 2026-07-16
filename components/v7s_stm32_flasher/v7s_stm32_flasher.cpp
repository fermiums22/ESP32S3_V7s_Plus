#include "v7s_stm32_flasher.h"
#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <algorithm>
#include <cstring>

namespace esphome::v7s_stm32_flasher {

static const char *const TAG = "v7s.stm32_flasher";

void STM32Flasher::setup() {
  this->boot_pin_->setup();
  this->reset_pin_->setup();
  this->boot_pin_->digital_write(false);
  this->reset_pin_->digital_write(true);  // open-drain release
  this->publish_("ready", 0.0f);
}

void STM32Flasher::dump_config() {
  ESP_LOGCONFIG(TAG, "V7s STM32 ROM flasher:");
  LOG_PIN("  BOOT0: ", this->boot_pin_);
  LOG_PIN("  NRST: ", this->reset_pin_);
  ESP_LOGCONFIG(TAG, "  Embedded firmware: %u bytes", static_cast<unsigned>(this->firmware_size_));
}

void STM32Flasher::publish_(const char *status, float progress) {
  ESP_LOGI(TAG, "%s (%.0f%%)", status, progress);
  if (this->status_ != nullptr)
    this->status_->publish_state(status);
  if (this->progress_ != nullptr)
    this->progress_->publish_state(progress);
}

void STM32Flasher::set_uart_(uint32_t baud, uart::UARTParityOptions parity) {
  this->parent_->flush();
  this->parent_->set_baud_rate(baud);
  this->parent_->set_data_bits(8);
  this->parent_->set_parity(parity);
  this->parent_->set_stop_bits(1);
  this->parent_->load_settings(false);
  this->drain_uart_();
}

void STM32Flasher::drain_uart_() {
  uint8_t byte;
  while (this->available() != 0)
    this->read_byte(&byte);
}

bool STM32Flasher::read_exact_(uint8_t *data, size_t size, uint32_t timeout_ms) {
  const uint32_t deadline = millis() + timeout_ms;
  size_t offset = 0;
  while (offset < size) {
    while (this->available() != 0 && offset < size) {
      if (!this->read_byte(&data[offset]))
        break;
      offset++;
    }
    if (offset == size)
      return true;
    if (static_cast<int32_t>(millis() - deadline) >= 0)
      return false;
    App.feed_wdt();
    delay(1);
  }
  return true;
}

bool STM32Flasher::wait_ack_(uint32_t timeout_ms) {
  uint8_t reply = 0;
  if (!this->read_exact_(&reply, 1, timeout_ms)) {
    ESP_LOGE(TAG, "STM32 bootloader ACK timeout");
    return false;
  }
  if (reply != ACK) {
    ESP_LOGE(TAG, "STM32 bootloader reply 0x%02X", reply);
    return false;
  }
  return true;
}

bool STM32Flasher::command_(uint8_t command, uint32_t timeout_ms) {
  const uint8_t frame[2] = {command, static_cast<uint8_t>(command ^ 0xFFU)};
  this->write_array(frame, sizeof(frame));
  this->parent_->flush();
  return this->wait_ack_(timeout_ms);
}

bool STM32Flasher::send_address_(uint32_t address) {
  uint8_t frame[5];
  frame[0] = static_cast<uint8_t>(address >> 24);
  frame[1] = static_cast<uint8_t>(address >> 16);
  frame[2] = static_cast<uint8_t>(address >> 8);
  frame[3] = static_cast<uint8_t>(address);
  frame[4] = frame[0] ^ frame[1] ^ frame[2] ^ frame[3];
  this->write_array(frame, sizeof(frame));
  this->parent_->flush();
  return this->wait_ack_(1000);
}

void STM32Flasher::reset_target_(bool bootloader) {
  this->boot_pin_->digital_write(bootloader);
  this->reset_pin_->digital_write(false);
  delay(20);
  this->reset_pin_->digital_write(true);
  delay(80);
}

void STM32Flasher::stop_robot_() {
  // Modbus FC05: slave 1, coil 0 OFF, CRC 0xCACD (low byte first).
  const uint8_t stop_frame[8] = {0x01, 0x05, 0x00, 0x00, 0x00, 0x00, 0xCD, 0xCA};
  this->write_array(stop_frame, sizeof(stop_frame));
  this->parent_->flush();
  delay(30);
}

bool STM32Flasher::enter_bootloader_() {
  this->publish_("entering bootloader", 1.0f);
  this->stop_robot_();
  this->controller_->stop_poller();
  delay(100);
  this->drain_uart_();
  this->reset_target_(true);
  this->set_uart_(BOOT_BAUD, uart::UART_CONFIG_PARITY_EVEN);

  const uint8_t sync = 0x7F;
  for (uint8_t attempt = 0; attempt < 3; attempt++) {
    this->drain_uart_();
    this->write_byte(sync);
    this->parent_->flush();
    if (this->wait_ack_(800))
      return true;
    this->reset_target_(true);
  }
  return false;
}

bool STM32Flasher::erase_() {
  this->publish_("erasing STM32", 3.0f);
  if (this->command_(0x44)) {
    const uint8_t mass_erase[3] = {0xFF, 0xFF, 0x00};
    this->write_array(mass_erase, sizeof(mass_erase));
    this->parent_->flush();
    return this->wait_ack_(15000);
  }

  // Some F0 bootloader revisions expose only the legacy erase command.
  this->drain_uart_();
  if (!this->command_(0x43))
    return false;
  const uint8_t mass_erase[2] = {0xFF, 0x00};
  this->write_array(mass_erase, sizeof(mass_erase));
  this->parent_->flush();
  return this->wait_ack_(15000);
}

bool STM32Flasher::write_firmware_() {
  uint8_t frame[BLOCK_SIZE + 2];
  size_t offset = 0;
  while (offset < this->firmware_size_) {
    size_t count = std::min(BLOCK_SIZE, this->firmware_size_ - offset);
    if ((count & 1U) != 0)
      count++;
    if (!this->command_(0x31) || !this->send_address_(FLASH_BASE + offset))
      return false;

    frame[0] = static_cast<uint8_t>(count - 1U);
    uint8_t checksum = frame[0];
    for (size_t i = 0; i < count; i++) {
      frame[1 + i] = (offset + i < this->firmware_size_) ? progmem_read_byte(this->firmware_ + offset + i) : 0xFF;
      checksum ^= frame[1 + i];
    }
    frame[1 + count] = checksum;
    this->write_array(frame, count + 2U);
    this->parent_->flush();
    if (!this->wait_ack_(3000))
      return false;
    offset += count;
    this->publish_("writing STM32", 5.0f + 70.0f * offset / this->firmware_size_);
    App.feed_wdt();
  }
  return true;
}

bool STM32Flasher::verify_firmware_() {
  uint8_t expected[BLOCK_SIZE];
  uint8_t actual[BLOCK_SIZE];
  size_t offset = 0;
  while (offset < this->firmware_size_) {
    const size_t count = std::min(BLOCK_SIZE, this->firmware_size_ - offset);
    if (!this->command_(0x11) || !this->send_address_(FLASH_BASE + offset))
      return false;
    const uint8_t length[2] = {static_cast<uint8_t>(count - 1U), static_cast<uint8_t>((count - 1U) ^ 0xFFU)};
    this->write_array(length, sizeof(length));
    this->parent_->flush();
    if (!this->wait_ack_(1000) || !this->read_exact_(actual, count, 2000))
      return false;
    progmem_memcpy(expected, this->firmware_ + offset, count);
    if (std::memcmp(expected, actual, count) != 0) {
      ESP_LOGE(TAG, "Verify failed at 0x%08X", static_cast<unsigned>(FLASH_BASE + offset));
      return false;
    }
    offset += count;
    this->publish_("verifying STM32", 75.0f + 24.0f * offset / this->firmware_size_);
    App.feed_wdt();
  }
  return true;
}

void STM32Flasher::finish_(bool success, const char *status) {
  this->boot_pin_->digital_write(false);
  this->reset_target_(false);
  this->set_uart_(APP_BAUD, uart::UART_CONFIG_PARITY_NONE);
  this->controller_->start_poller();
  this->running_ = false;
  this->publish_(status, success ? 100.0f : 0.0f);
  if (success)
    this->status_clear_error();
  else
    this->status_set_error();
}

void STM32Flasher::flash() {
  if (this->running_) {
    ESP_LOGW(TAG, "STM32 flash already running");
    return;
  }
  if (this->firmware_ == nullptr || this->firmware_size_ == 0 || this->firmware_size_ > 65536) {
    this->finish_(false, "invalid firmware");
    return;
  }
  this->running_ = true;
  this->publish_("starting STM32 update", 0.0f);
  if (!this->enter_bootloader_()) {
    this->finish_(false, "bootloader connection failed");
    return;
  }
  if (!this->erase_()) {
    this->finish_(false, "erase failed");
    return;
  }
  if (!this->write_firmware_()) {
    this->finish_(false, "write failed");
    return;
  }
  if (!this->verify_firmware_()) {
    this->finish_(false, "verify failed");
    return;
  }
  this->finish_(true, "STM32 update complete");
}

}  // namespace esphome::v7s_stm32_flasher
