#include "pcm_i2s_tx.h"

#include "esphome/core/log.h"

#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_spiffs.h"
#include <algorithm>
#include <cstdio>
#include <cstring>

namespace esphome::pcm_i2s_tx {

static const char *const TAG = "pcm_i2s_tx";

void PcmI2sTx::setup() {
  const esp_vfs_spiffs_conf_t fs_config = {
      .base_path = "/audio",
      .partition_label = "audio",
      .max_files = 2,
      .format_if_mount_failed = false,
  };
  const esp_err_t fs_err = esp_vfs_spiffs_register(&fs_config);
  if (fs_err != ESP_OK) {
    ESP_LOGE(TAG, "Cannot mount audio SPIFFS: %s", esp_err_to_name(fs_err));
  } else {
    FILE *probe = fopen("/audio/Balensiaga.mp3", "rb");
    if (probe != nullptr) {
      fseek(probe, 0, SEEK_END);
      ESP_LOGI(TAG, "Audio SPIFFS ready: Balensiaga.mp3, %ld bytes", ftell(probe));
      fclose(probe);
    } else {
      ESP_LOGE(TAG, "Audio SPIFFS mounted, but Balensiaga.mp3 is missing");
    }
  }

  this->buffer_ = static_cast<uint8_t *>(heap_caps_malloc(BUFFER_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (this->buffer_ == nullptr) {
    ESP_LOGE(TAG, "Cannot allocate %u-byte PSRAM PCM buffer", static_cast<unsigned>(BUFFER_SIZE));
    this->mark_failed();
    return;
  }
  if (xTaskCreate(i2s_task_, "pcm_i2s", 6144, this, 10, nullptr) != pdPASS) {
    ESP_LOGE(TAG, "Cannot create PCM bridge tasks");
    this->mark_failed();
  }
}

audio::AudioFile *PcmI2sTx::get_local_audio_file() {
  if (this->local_audio_data_ != nullptr)
    return &this->local_audio_file_;

  FILE *file = fopen("/audio/Balensiaga.mp3", "rb");
  if (file == nullptr) {
    ESP_LOGE(TAG, "Local track /audio/Balensiaga.mp3 not found");
    return nullptr;
  }
  fseek(file, 0, SEEK_END);
  const long size = ftell(file);
  rewind(file);
  if (size <= 0) {
    fclose(file);
    ESP_LOGE(TAG, "Local track is empty");
    return nullptr;
  }
  auto *data = static_cast<uint8_t *>(
      heap_caps_malloc(static_cast<size_t>(size), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (data == nullptr || fread(data, 1, static_cast<size_t>(size), file) != static_cast<size_t>(size)) {
    fclose(file);
    free(data);
    ESP_LOGE(TAG, "Cannot load %ld-byte local track into PSRAM", size);
    return nullptr;
  }
  fclose(file);
  this->local_audio_data_ = data;
  this->local_audio_file_ = {data, static_cast<size_t>(size), audio::AudioFileType::MP3};
  ESP_LOGI(TAG, "Loaded local track from SPIFFS: %ld bytes", size);
  return &this->local_audio_file_;
}

void PcmI2sTx::loop() {
  if (millis() - this->last_report_ < 10000)
    return;
  this->last_report_ = millis();
  const uint32_t read = this->read_.load(std::memory_order_acquire);
  const uint32_t write = this->write_.load(std::memory_order_acquire);
  ESP_LOGI(TAG, "PCM 10 s: net=%u i2s=%u fill=%u i2s_nz=%u underruns=%u streaming=%s",
           static_cast<unsigned>(this->network_bytes_.exchange(0, std::memory_order_relaxed)),
           static_cast<unsigned>(this->i2s_bytes_.exchange(0, std::memory_order_relaxed)),
           static_cast<unsigned>(write - read),
           static_cast<unsigned>(this->i2s_nonzero_.exchange(0, std::memory_order_relaxed)),
           static_cast<unsigned>(this->underruns_.exchange(0, std::memory_order_relaxed)),
           YESNO(this->streaming_.load(std::memory_order_acquire)));
}

void PcmI2sTx::dump_config() {
  ESP_LOGCONFIG(TAG, "PCM I2S slave TX:");
  ESP_LOGCONFIG(TAG, "  Input: ESPHome HTTP audio pipeline");
  ESP_LOGCONFIG(TAG, "  Local track: /audio/Balensiaga.mp3 (SPIFFS)");
  ESP_LOGCONFIG(TAG, "  I2S output: S16LE stereo 44100 Hz");
  ESP_LOGCONFIG(TAG, "  BCLK: GPIO5, WS: GPIO6, DATA: GPIO7");
  ESP_LOGCONFIG(TAG, "  PSRAM buffer: %u bytes", static_cast<unsigned>(BUFFER_SIZE));
}

size_t PcmI2sTx::play(const uint8_t *data, size_t length) {
  return this->play(data, length, 0);
}

size_t PcmI2sTx::play(const uint8_t *data, size_t length, TickType_t ticks_to_wait) {
  if (!this->streaming_.load(std::memory_order_acquire))
    this->start();
  const TickType_t started = xTaskGetTickCount();
  size_t copied = 0;
  while (copied < length) {
    const uint32_t read = this->read_.load(std::memory_order_acquire);
    uint32_t write = this->write_.load(std::memory_order_relaxed);
    const uint32_t free = BUFFER_SIZE - (write - read);
    if (free == 0) {
      if (ticks_to_wait == 0 || xTaskGetTickCount() - started >= ticks_to_wait)
        break;
      vTaskDelay(1);
      continue;
    }
    const uint32_t count = std::min<uint32_t>(free, length - copied);
    const uint32_t offset = write % BUFFER_SIZE;
    const uint32_t first = std::min<uint32_t>(count, BUFFER_SIZE - offset);
    memcpy(&this->buffer_[offset], data + copied, first);
    if (count > first)
      memcpy(this->buffer_, data + copied + first, count - first);
    copied += count;
    write += count;
    this->write_.store(write, std::memory_order_release);
  }
  this->network_bytes_.fetch_add(copied, std::memory_order_relaxed);
  return copied;
}

void PcmI2sTx::start() {
  this->read_.store(0, std::memory_order_relaxed);
  this->write_.store(0, std::memory_order_relaxed);
  this->finishing_.store(false, std::memory_order_release);
  this->streaming_.store(true, std::memory_order_release);
  this->state_ = speaker::STATE_RUNNING;
}

void PcmI2sTx::stop() {
  this->streaming_.store(false, std::memory_order_release);
  this->finishing_.store(false, std::memory_order_release);
  this->read_.store(this->write_.load(std::memory_order_acquire), std::memory_order_release);
  this->state_ = speaker::STATE_STOPPED;
}

void PcmI2sTx::finish() { this->finishing_.store(true, std::memory_order_release); }

bool PcmI2sTx::has_buffered_data() const {
  return this->write_.load(std::memory_order_acquire) != this->read_.load(std::memory_order_acquire);
}

void PcmI2sTx::i2s_task_(void *arg) {
  auto *self = static_cast<PcmI2sTx *>(arg);
  i2s_chan_handle_t tx = nullptr;
  i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_SLAVE);
  channel_config.dma_desc_num = 6;
  channel_config.dma_frame_num = 240;
  esp_err_t err = i2s_new_channel(&channel_config, &tx, nullptr);
  i2s_std_config_t config = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(44100),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,
          .bclk = GPIO_NUM_5,
          .ws = GPIO_NUM_6,
          .dout = GPIO_NUM_7,
          .din = I2S_GPIO_UNUSED,
          .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
      },
  };
  if (err == ESP_OK)
    err = i2s_channel_init_std_mode(tx, &config);
  if (err == ESP_OK)
    err = i2s_channel_enable(tx);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "I2S TX init failed: %s", esp_err_to_name(err));
    if (tx != nullptr)
      i2s_del_channel(tx);
    vTaskDelete(nullptr);
    return;
  }
  ESP_LOGI(TAG, "I2S slave TX ready");

  alignas(4) uint8_t block[1024]{};
  size_t test_audio_pos = self->test_audio_size_;
  uint32_t seen_test_request = 0;
  while (true) {
    uint32_t count = 0;
    if (self->streaming_.load(std::memory_order_acquire)) {
      uint32_t read = self->read_.load(std::memory_order_relaxed);
      const uint32_t write = self->write_.load(std::memory_order_acquire);
      count = std::min<uint32_t>(write - read, sizeof(block));
      const uint32_t offset = read % BUFFER_SIZE;
      const uint32_t first = std::min<uint32_t>(count, BUFFER_SIZE - offset);
      memcpy(block, &self->buffer_[offset], first);
      if (count > first)
        memcpy(block + first, self->buffer_, count - first);
      self->read_.store(read + count, std::memory_order_release);
      if (count < sizeof(block))
        self->underruns_.fetch_add(1, std::memory_order_relaxed);
      if (count == 0 && self->finishing_.load(std::memory_order_acquire))
        self->stop();
    } else if (self->test_audio_ != nullptr) {
      const uint32_t request = self->test_audio_request_.load(std::memory_order_acquire);
      if (request != seen_test_request) {
        seen_test_request = request;
        test_audio_pos = 0;
        ESP_LOGI(TAG, "Embedded test track started");
      }
      if (test_audio_pos < self->test_audio_size_) {
        const size_t input_count = std::min<size_t>(sizeof(block) / 8, self->test_audio_size_ - test_audio_pos);
        auto *samples = reinterpret_cast<int16_t *>(block);
        for (size_t i = 0; i < input_count; i++) {
          const int16_t sample =
              static_cast<int16_t>((static_cast<int16_t>(self->test_audio_[test_audio_pos++]) - 128) * 256);
          samples[i * 4 + 0] = sample;
          samples[i * 4 + 1] = sample;
          samples[i * 4 + 2] = sample;
          samples[i * 4 + 3] = sample;
        }
        count = input_count * 8;
        if (test_audio_pos == self->test_audio_size_)
          ESP_LOGI(TAG, "Embedded test track finished");
      }
    }
    if (count < sizeof(block))
      memset(block + count, 0, sizeof(block) - count);
    uint32_t nonzero = 0;
    for (size_t i = 0; i < sizeof(block); i++)
      nonzero += block[i] != 0;
    size_t written = 0;
    err = i2s_channel_write(tx, block, sizeof(block), &written, 1000);
    if (err == ESP_OK) {
      self->i2s_bytes_.fetch_add(written, std::memory_order_relaxed);
      self->i2s_nonzero_.fetch_add(nonzero, std::memory_order_relaxed);
    }
  }
}

}  // namespace esphome::pcm_i2s_tx
