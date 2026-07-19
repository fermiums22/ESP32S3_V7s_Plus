#include "v7s_stm32_flasher.h"

#include "esphome/components/web_server_base/web_server_base.h"
#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <esp_spiffs.h>
#include <algorithm>
#include <cerrno>
#include <cstring>

namespace esphome::v7s_stm32_flasher {

static const char *const TAG = "v7s.stm32_flasher";
static const char *const UPLOAD_PATH = "/api/v1/stm32-firmware";

class FirmwareUploadHandler : public AsyncWebHandler {
 public:
  explicit FirmwareUploadHandler(STM32Flasher *parent) : parent_(parent) {}

  bool canHandle(AsyncWebServerRequest *request) const override {
    if (request->method() != HTTP_POST)
      return false;
    char url_buf[AsyncWebServerRequest::URL_BUF_SIZE];
    request->url_to(url_buf);
    return std::strcmp(url_buf, UPLOAD_PATH) == 0;
  }

  void handleUpload(AsyncWebServerRequest *request, const std::string &filename, size_t index, uint8_t *data,
                    size_t len, bool final) override {
    // ESPHome's multipart reader sends a zero-length start marker before the
    // first chunk. Keep one state machine for both that marker and data chunks.
    if (index == 0 && !this->started_) {
      this->started_ = true;
      const auto token = request->get_header("X-V7S-Token");
      this->parent_->begin_upload_(filename, token.value_or(""));
    }
    if (len != 0)
      this->parent_->write_upload_(index, data, len);
    if (final) {
      this->parent_->finish_upload_();
      this->started_ = false;
    }
  }

  void handleRequest(AsyncWebServerRequest *request) override {
    request->send(this->parent_->upload_http_status_, "text/plain", this->parent_->upload_response_.c_str());
  }

  bool isRequestHandlerTrivial() const override { return false; }

 protected:
  STM32Flasher *parent_;
  bool started_{false};
};

void STM32Flasher::setup() {
  this->boot_pin_->setup();
  this->reset_pin_->setup();
  this->boot_pin_->digital_write(false);
  this->reset_pin_->digital_write(true);  // open-drain release
  this->publish_("ready", 0.0f);

  // Always expose the endpoint. If the upload slot cannot be mounted it returns
  // a useful 503 instead of looking like an unknown route (404).
  auto *web_server = web_server_base::global_web_server_base;
  if (web_server == nullptr) {
    ESP_LOGE(TAG, "Web server is unavailable; STM32 image upload is disabled");
    this->publish_upload_status_("upload endpoint unavailable");
  } else {
    // add_handler() applies ESPHome's configured web-server Basic Auth first.
    web_server->add_handler(new FirmwareUploadHandler(this));  // NOLINT(cppcoreguidelines-owning-memory)
    ESP_LOGI(TAG, "STM32 upload endpoint ready: POST %s", UPLOAD_PATH);
  }

  if (!this->mount_filesystem_()) {
    this->publish_upload_status_("staging unavailable");
    return;
  }

  // A staged image is deliberately transient: never reuse it across an S3
  // restart, even if a previous HTTP request had completed successfully.
  this->invalidate_staging_();
  this->publish_upload_status_("no staged image; upload one before flashing STM32");
  if (this->upload_size_ != nullptr)
    this->upload_size_->publish_state(0);

}

void STM32Flasher::dump_config() {
  ESP_LOGCONFIG(TAG, "V7s STM32 ROM flasher:");
  LOG_PIN("  BOOT0: ", this->boot_pin_);
  LOG_PIN("  NRST: ", this->reset_pin_);
  ESP_LOGCONFIG(TAG, "  Transient staging: %s", this->filesystem_mounted_ ? "audio SPIFFS" : "unavailable");
}

bool STM32Flasher::mount_filesystem_() {
  // pcm_i2s_tx mounts label "audio" at setup_priority::AFTER_WIFI. This
  // component runs immediately afterwards and deliberately does not register
  // (or format) the shared filesystem a second time.
  size_t total = 0;
  size_t used = 0;
  const esp_err_t err = esp_spiffs_info(FS_PARTITION_LABEL, &total, &used);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "PCM audio SPIFFS is not mounted (%s)", esp_err_to_name(err));
    return false;
  }
  this->filesystem_mounted_ = true;
  ESP_LOGI(TAG, "Using audio SPIFFS for STM32 images: %u/%u bytes used", static_cast<unsigned>(used),
           static_cast<unsigned>(total));
  return true;
}

uint32_t STM32Flasher::crc32_update_(uint32_t crc, const uint8_t *data, size_t size) {
  for (size_t i = 0; i < size; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++)
      crc = (crc >> 1U) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
  }
  return crc;
}

uint32_t STM32Flasher::read_le32_(const uint8_t *data) {
  return static_cast<uint32_t>(data[0]) | (static_cast<uint32_t>(data[1]) << 8U) |
         (static_cast<uint32_t>(data[2]) << 16U) | (static_cast<uint32_t>(data[3]) << 24U);
}

bool STM32Flasher::validate_firmware_file_(const char *path, size_t *size, uint32_t *crc) const {
  FILE *file = fopen(path, "rb");
  if (file == nullptr)
    return false;

  uint8_t vector_table[8];
  const size_t vector_read = fread(vector_table, 1, sizeof(vector_table), file);
  if (vector_read != sizeof(vector_table)) {
    fclose(file);
    return false;
  }
  const uint32_t stack_pointer = read_le32_(vector_table);
  const uint32_t reset_handler = read_le32_(vector_table + 4);
  // STM32F071 SRAM begins at 0x20000000. Permit the whole 128 KiB F0 SRAM
  // range here; the exact part-specific linker limit is intentionally not
  // hard-coded into the upload protocol.
  const bool valid_stack = stack_pointer >= 0x20000000U && stack_pointer < 0x20020000U && (stack_pointer & 3U) == 0;
  const uint32_t reset_address = reset_handler & ~1U;
  const bool valid_reset = (reset_handler & 1U) != 0 && reset_address >= FLASH_BASE && reset_address < FLASH_END;
  if (!valid_stack || !valid_reset) {
    fclose(file);
    return false;
  }

  rewind(file);
  uint8_t buffer[BLOCK_SIZE];
  size_t total = 0;
  uint32_t calculated_crc = 0xFFFFFFFFU;
  while (true) {
    const size_t count = fread(buffer, 1, sizeof(buffer), file);
    if (count != 0) {
      total += count;
      if (total > MAX_FIRMWARE_SIZE) {
        fclose(file);
        return false;
      }
      calculated_crc = crc32_update_(calculated_crc, buffer, count);
    }
    if (count != sizeof(buffer)) {
      if (ferror(file) != 0) {
        fclose(file);
        return false;
      }
      break;
    }
  }
  fclose(file);
  if (total < sizeof(vector_table))
    return false;
  *size = total;
  *crc = ~calculated_crc;
  return true;
}

bool STM32Flasher::validate_staged_firmware_(size_t *size, uint32_t *crc) const {
  return this->filesystem_mounted_ && this->staging_valid_ &&
         this->validate_firmware_file_(STAGING_PATH, size, crc) && *size == this->staged_firmware_size_ &&
         *crc == this->staged_firmware_crc_;
}

void STM32Flasher::invalidate_staging_() {
  this->staging_valid_ = false;
  this->staged_firmware_size_ = 0;
  this->staged_firmware_crc_ = 0;
  if (!this->filesystem_mounted_)
    return;
  FILE *file = fopen(STAGING_PATH, "wb");
  if (file == nullptr) {
    ESP_LOGW(TAG, "Cannot clear staged STM32 image %s: errno=%d (%s)", STAGING_PATH, errno, strerror(errno));
    return;
  }
  if (fclose(file) != 0)
    ESP_LOGW(TAG, "Cannot close staged STM32 image %s: errno=%d (%s)", STAGING_PATH, errno, strerror(errno));
}

void STM32Flasher::publish_(const char *status, float progress) {
  ESP_LOGI(TAG, "%s (%.0f%%)", status, progress);
  if (this->status_ != nullptr)
    this->status_->publish_state(status);
  if (this->progress_ != nullptr)
    this->progress_->publish_state(progress);
}

void STM32Flasher::publish_upload_status_(const std::string &message) {
  ESP_LOGI(TAG, "Upload slot: %s", message.c_str());
  if (this->upload_status_ != nullptr)
    this->upload_status_->publish_state(message);
}

void STM32Flasher::set_upload_result_(int http_status, const std::string &message) {
  this->upload_http_status_ = http_status;
  this->upload_response_ = message;
}

bool STM32Flasher::upload_authorized_(const std::string &token) const {
  if (token.size() != this->upload_token_.size())
    return false;
  uint8_t difference = 0;
  for (size_t i = 0; i < token.size(); i++)
    difference |= static_cast<uint8_t>(token[i] ^ this->upload_token_[i]);
  return difference == 0;
}

void STM32Flasher::abort_upload_() {
  if (this->upload_file_ != nullptr) {
    fclose(this->upload_file_);
    this->upload_file_ = nullptr;
  }
  this->staging_valid_ = false;
}

void STM32Flasher::begin_upload_(const std::string &filename, const std::string &token) {
  this->abort_upload_();
  this->upload_failed_ = false;
  this->upload_bytes_ = 0;
  this->upload_crc_ = 0xFFFFFFFFU;
  this->set_upload_result_(400, "upload did not contain firmware data");

  if (this->running_) {
    this->upload_failed_ = true;
    this->set_upload_result_(409, "STM32 flash is in progress; retry after it finishes");
    return;
  }
  if (!this->filesystem_mounted_) {
    this->upload_failed_ = true;
    this->set_upload_result_(503, "STM32 upload slot is unavailable");
    return;
  }
  if (this->upload_token_.empty()) {
    this->upload_failed_ = true;
    this->set_upload_result_(503, "STM32 upload token is not configured");
    return;
  }
  if (!this->upload_authorized_(token)) {
    this->upload_failed_ = true;
    this->set_upload_result_(401, "invalid STM32 upload token");
    this->publish_upload_status_("upload rejected: invalid token");
    return;
  }
  // The standard web-server Basic Auth middleware runs before this handler.
  // This second token keeps the firmware endpoint protected even if web UI
  // authentication is intentionally disabled later. The file is staging only:
  // no image is retained after an S3 restart or an STM32 flash attempt.
  this->upload_file_ = fopen(STAGING_PATH, "wb");
  if (this->upload_file_ == nullptr) {
    this->upload_failed_ = true;
    char message[160];
    snprintf(message, sizeof(message), "cannot write STM32 staging file: errno=%d (%s)", errno, strerror(errno));
    this->set_upload_result_(507, message);
    this->publish_upload_status_("upload failed: cannot open staging file");
    return;
  }
  this->publish_upload_status_("receiving " + filename);
}

void STM32Flasher::write_upload_(size_t index, const uint8_t *data, size_t size) {
  if (this->upload_failed_ || this->upload_file_ == nullptr)
    return;
  if (index != this->upload_bytes_) {
    this->upload_failed_ = true;
    this->set_upload_result_(400, "non-contiguous upload rejected");
    this->abort_upload_();
    return;
  }
  if (size > MAX_FIRMWARE_SIZE - this->upload_bytes_) {
    this->upload_failed_ = true;
    this->set_upload_result_(413, "STM32 firmware exceeds 65536 bytes");
    this->abort_upload_();
    this->publish_upload_status_("upload rejected: image exceeds 65536 bytes");
    return;
  }
  if (fwrite(data, 1, size, this->upload_file_) != size) {
    this->upload_failed_ = true;
    this->set_upload_result_(507, "failed writing STM32 firmware file");
    this->abort_upload_();
    this->publish_upload_status_("upload failed while writing flash");
    return;
  }
  this->upload_bytes_ += size;
  this->upload_crc_ = crc32_update_(this->upload_crc_, data, size);
}

void STM32Flasher::finish_upload_() {
  if (this->upload_file_ != nullptr) {
    fclose(this->upload_file_);
    this->upload_file_ = nullptr;
  }
  if (this->upload_failed_) {
    return;
  }

  size_t size = 0;
  uint32_t crc = 0;
  if (!this->validate_firmware_file_(STAGING_PATH, &size, &crc) || size != this->upload_bytes_ ||
      crc != ~this->upload_crc_) {
    this->set_upload_result_(400, "invalid STM32 image (size, CRC32 or vector table)");
    this->publish_upload_status_("upload rejected: invalid STM32 image");
    return;
  }
  this->staging_valid_ = true;
  this->staged_firmware_size_ = size;
  this->staged_firmware_crc_ = crc;
  if (this->upload_size_ != nullptr)
    this->upload_size_->publish_state(size);
  char message[120];
  snprintf(message, sizeof(message), "staged image ready: %u B, CRC32 %08X", static_cast<unsigned>(size),
           static_cast<unsigned>(crc));
  this->publish_upload_status_(message);
  this->set_upload_result_(200, std::string("accepted: ") + message);
}

bool STM32Flasher::prepare_firmware_() {
  this->close_active_firmware_();
  this->active_source_ = FirmwareSource::NONE;
  this->active_firmware_size_ = 0;

  size_t staged_size = 0;
  uint32_t staged_crc = 0;
  if (this->validate_staged_firmware_(&staged_size, &staged_crc)) {
    this->active_file_ = fopen(STAGING_PATH, "rb");
    if (this->active_file_ != nullptr) {
      this->active_source_ = FirmwareSource::STAGED;
      this->active_firmware_size_ = staged_size;
      return true;
    }
  }
  return false;
}

bool STM32Flasher::read_firmware_block_(size_t offset, uint8_t *data, size_t size) {
  if (this->active_source_ != FirmwareSource::STAGED || this->active_file_ == nullptr)
    return false;
  return fseek(this->active_file_, static_cast<long>(offset), SEEK_SET) == 0 &&
         fread(data, 1, size, this->active_file_) == size;
}

void STM32Flasher::close_active_firmware_() {
  if (this->active_file_ != nullptr) {
    fclose(this->active_file_);
    this->active_file_ = nullptr;
  }
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
  while (offset < this->active_firmware_size_) {
    const size_t data_count = std::min(BLOCK_SIZE, this->active_firmware_size_ - offset);
    size_t count = data_count;
    if ((count & 1U) != 0)
      count++;
    if (!this->command_(0x31) || !this->send_address_(FLASH_BASE + offset))
      return false;

    frame[0] = static_cast<uint8_t>(count - 1U);
    uint8_t checksum = frame[0];
    if (!this->read_firmware_block_(offset, frame + 1, data_count))
      return false;
    if (count != data_count)
      frame[1 + data_count] = 0xFF;
    for (size_t i = 0; i < count; i++)
      checksum ^= frame[1 + i];
    frame[1 + count] = checksum;
    this->write_array(frame, count + 2U);
    this->parent_->flush();
    if (!this->wait_ack_(3000))
      return false;
    offset += count;
    this->publish_("writing STM32", 5.0f + 70.0f * offset / this->active_firmware_size_);
    App.feed_wdt();
  }
  return true;
}

bool STM32Flasher::verify_firmware_() {
  uint8_t expected[BLOCK_SIZE];
  uint8_t actual[BLOCK_SIZE];
  size_t offset = 0;
  while (offset < this->active_firmware_size_) {
    const size_t count = std::min(BLOCK_SIZE, this->active_firmware_size_ - offset);
    if (!this->command_(0x11) || !this->send_address_(FLASH_BASE + offset))
      return false;
    const uint8_t length[2] = {static_cast<uint8_t>(count - 1U), static_cast<uint8_t>((count - 1U) ^ 0xFFU)};
    this->write_array(length, sizeof(length));
    this->parent_->flush();
    if (!this->wait_ack_(1000) || !this->read_exact_(actual, count, 2000) ||
        !this->read_firmware_block_(offset, expected, count))
      return false;
    if (std::memcmp(expected, actual, count) != 0) {
      ESP_LOGE(TAG, "Verify failed at 0x%08X", static_cast<unsigned>(FLASH_BASE + offset));
      return false;
    }
    offset += count;
    this->publish_("verifying STM32", 75.0f + 24.0f * offset / this->active_firmware_size_);
    App.feed_wdt();
  }
  return true;
}

void STM32Flasher::finish_(bool success, const char *status) {
  this->boot_pin_->digital_write(false);
  this->reset_target_(false);
  this->set_uart_(APP_BAUD, uart::UART_CONFIG_PARITY_NONE);
  this->controller_->start_poller();
  this->close_active_firmware_();
  this->active_source_ = FirmwareSource::NONE;
  this->active_firmware_size_ = 0;
  this->invalidate_staging_();
  if (this->upload_size_ != nullptr)
    this->upload_size_->publish_state(0);
  this->publish_upload_status_("staged image cleared after STM32 flash attempt");
  this->running_ = false;
  this->publish_(status, success ? 100.0f : 0.0f);
  if (success)
    this->status_clear_error();
  else
    this->status_set_error();
}

void STM32Flasher::flash_() {
  if (this->running_) {
    ESP_LOGW(TAG, "STM32 flash already running");
    return;
  }
  if (!this->prepare_firmware_()) {
    this->invalidate_staging_();
    if (this->upload_size_ != nullptr)
      this->upload_size_->publish_state(0);
    this->publish_upload_status_("no valid staged image");
    this->publish_("no valid staged STM32 image", 0.0f);
    this->status_set_error();
    return;
  }
  this->running_ = true;
  this->publish_("starting staged STM32 update", 0.0f);
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

void STM32Flasher::flash() { this->flash_(); }

}  // namespace esphome::v7s_stm32_flasher
