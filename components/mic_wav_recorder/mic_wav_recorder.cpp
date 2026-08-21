#include "mic_wav_recorder.h"

#include "esphome/core/log.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

#ifdef USE_ESP32
#include <esp_heap_caps.h>
#endif

namespace esphome::mic_wav_recorder {

static const char *const TAG = "mic_wav_recorder";

void MicWavRecorder::setup() {
  if (this->microphone_ == nullptr) {
    ESP_LOGE(TAG, "Microphone is not configured");
    this->mark_failed();
    return;
  }

#ifdef USE_ESP32
  this->recording_ = static_cast<uint8_t *>(heap_caps_malloc(RECORD_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
#endif
  if (this->recording_ == nullptr) {
    ESP_LOGE(TAG, "Unable to allocate %u-byte PSRAM recording buffer", static_cast<unsigned>(RECORD_BYTES));
    this->mark_failed();
    return;
  }

  this->microphone_->add_data_callback(
      [this](const std::vector<uint8_t> &data) { this->process_audio_(data); });
  ESP_LOGI(TAG, "Armed for one 10-second stereo recording");
}

void MicWavRecorder::process_audio_(const std::vector<uint8_t> &data) {
  if (this->recording_ready_.load(std::memory_order_relaxed) || this->recording_ == nullptr)
    return;

  this->recording_started_.store(true, std::memory_order_release);
  const size_t copy_bytes = std::min(data.size(), RECORD_BYTES - this->captured_bytes_);
  std::memcpy(this->recording_ + this->captured_bytes_, data.data(), copy_bytes);
  this->captured_bytes_ += copy_bytes;
  if (this->captured_bytes_ == RECORD_BYTES)
    this->recording_ready_.store(true, std::memory_order_release);
}

void MicWavRecorder::loop() {
  if (this->dump_finished_)
    return;

  if (!this->recording_announced_ && this->recording_started_.load(std::memory_order_acquire)) {
    ESP_LOGI(TAG, "MIC_WAV_RECORDING");
    this->recording_announced_ = true;
  }

  if (!this->recording_ready_.load(std::memory_order_acquire))
    return;

  if (!this->dump_started_) {
    ESP_LOGI(TAG, "MIC_WAV_BEGIN BYTES=%u RATE=%u CHANNELS=%u BITS=16", static_cast<unsigned>(RECORD_BYTES),
             static_cast<unsigned>(SAMPLE_RATE), static_cast<unsigned>(CHANNEL_COUNT));
    this->dump_started_ = true;
  }

  for (size_t chunk_index = 0; chunk_index < DUMP_CHUNKS_PER_LOOP && this->dump_offset_ < RECORD_BYTES;
       chunk_index++) {
    const size_t chunk_bytes = std::min(DUMP_CHUNK_BYTES, RECORD_BYTES - this->dump_offset_);
    char line[DUMP_CHUNK_BYTES * 2 + 1];
    for (size_t index = 0; index < chunk_bytes; index++)
      std::snprintf(line + index * 2, sizeof(line) - index * 2, "%02X", this->recording_[this->dump_offset_ + index]);
    line[chunk_bytes * 2] = '\0';
    ESP_LOGI(TAG, "MIC_WAV %06X:%s", static_cast<unsigned>(this->dump_offset_), line);
    this->dump_offset_ += chunk_bytes;
  }

  if (this->dump_offset_ == RECORD_BYTES) {
    ESP_LOGI(TAG, "MIC_WAV_END");
#ifdef USE_ESP32
    heap_caps_free(this->recording_);
#endif
    this->recording_ = nullptr;
    this->dump_finished_ = true;
  }
}

void MicWavRecorder::dump_config() {
  ESP_LOGCONFIG(TAG, "10-second stereo WAV recorder:");
  ESP_LOGCONFIG(TAG, "  Buffer: %u bytes PSRAM", static_cast<unsigned>(RECORD_BYTES));
}

}  // namespace esphome::mic_wav_recorder
