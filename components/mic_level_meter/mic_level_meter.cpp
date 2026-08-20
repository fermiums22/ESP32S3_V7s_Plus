#include "mic_level_meter.h"

#include "esphome/core/log.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace esphome::mic_level_meter {

static const char *const TAG = "mic_level_meter";

void MicLevelMeter::setup() {
  if (this->microphone_ == nullptr) {
    ESP_LOGE(TAG, "Microphone is not configured");
    this->mark_failed();
    return;
  }

  this->microphone_->add_data_callback(
      [this](const std::vector<uint8_t> &data) { this->process_audio_(data); });

  // Let the GPIO switch turn the microphone board on before starting I2S.
  this->set_timeout(250, [this]() {
    ESP_LOGI(TAG, "Starting stereo PDM microphone test");
    this->microphone_->start();
  });
}

void MicLevelMeter::process_audio_(const std::vector<uint8_t> &data) {
  // ESPHome supplies stereo 16-bit PCM as L, R, L, R ... frames.
  const size_t frame_count = data.size() / (2 * sizeof(int16_t));
  const auto *samples = reinterpret_cast<const int16_t *>(data.data());

  int64_t local_sum[2]{0, 0};
  uint64_t local_square_sum[2]{0, 0};
  uint16_t local_peak[2]{0, 0};

  for (size_t frame = 0; frame < frame_count; frame++) {
    for (size_t channel = 0; channel < 2; channel++) {
      const int32_t sample = samples[frame * 2 + channel];
      const uint16_t magnitude = static_cast<uint16_t>(std::min<int32_t>(std::abs(sample), 32767));
      local_sum[channel] += sample;
      local_square_sum[channel] += static_cast<uint64_t>(sample * sample);
      local_peak[channel] = std::max(local_peak[channel], magnitude);
    }
  }

#ifdef USE_ESP32
  portENTER_CRITICAL(&this->data_lock_);
#endif
  for (size_t channel = 0; channel < 2; channel++) {
    this->sum_[channel] += local_sum[channel];
    this->square_sum_[channel] += local_square_sum[channel];
    this->sample_count_[channel] += frame_count;
    this->peak_[channel] = std::max(this->peak_[channel], local_peak[channel]);
  }
  if (this->capture_delay_updates_ == 0 && !this->capture_ready_) {
    const size_t available_samples = frame_count * 2;
    const size_t remaining_samples = CAPTURE_SAMPLE_COUNT - this->capture_sample_count_;
    const size_t copy_count = std::min(available_samples, remaining_samples);
    std::copy_n(samples, copy_count, this->capture_samples_.begin() + this->capture_sample_count_);
    this->capture_sample_count_ += copy_count;
    this->capture_ready_ = this->capture_sample_count_ == CAPTURE_SAMPLE_COUNT;
  }
#ifdef USE_ESP32
  portEXIT_CRITICAL(&this->data_lock_);
#endif
}

void MicLevelMeter::update() {
  int64_t sum[2];
  uint64_t square_sum[2];
  uint32_t sample_count[2];
  uint16_t peak[2];
  std::array<int16_t, CAPTURE_SAMPLE_COUNT> capture_samples{};
  size_t capture_sample_count = 0;

#ifdef USE_ESP32
  portENTER_CRITICAL(&this->data_lock_);
#endif
  for (size_t channel = 0; channel < 2; channel++) {
    sum[channel] = this->sum_[channel];
    square_sum[channel] = this->square_sum_[channel];
    sample_count[channel] = this->sample_count_[channel];
    peak[channel] = this->peak_[channel];
    this->sum_[channel] = 0;
    this->square_sum_[channel] = 0;
    this->sample_count_[channel] = 0;
    this->peak_[channel] = 0;
  }
  if (this->capture_ready_) {
    capture_samples = this->capture_samples_;
    capture_sample_count = this->capture_sample_count_;
    this->capture_sample_count_ = 0;
    this->capture_ready_ = false;
    // Re-arm roughly two seconds later so a PC utility can attach at any time.
    this->capture_delay_updates_ = 20;
  } else if (this->capture_delay_updates_ > 0) {
    this->capture_delay_updates_--;
  }
#ifdef USE_ESP32
  portEXIT_CRITICAL(&this->data_lock_);
#endif

  uint32_t rms[2]{0, 0};
  for (size_t channel = 0; channel < 2; channel++) {
    if (sample_count[channel] == 0)
      continue;

    // Subtract the channel's mean so a DC offset does not look like sound.
    const double mean = static_cast<double>(sum[channel]) / sample_count[channel];
    const double mean_square = static_cast<double>(square_sum[channel]) / sample_count[channel];
    const double variance = std::max(0.0, mean_square - mean * mean);
    rms[channel] = static_cast<uint32_t>(std::lround(std::sqrt(variance)));
  }

  // Machine-readable marker consumed by tools/mic_level_console.py.
  ESP_LOGI(TAG, "MIC_LEVEL L=%" PRIu32 " R=%" PRIu32 " LP=%u RP=%u N=%" PRIu32, rms[0], rms[1], peak[0],
           peak[1], std::min(sample_count[0], sample_count[1]));

  if (capture_sample_count == CAPTURE_SAMPLE_COUNT) {
    ESP_LOGI(TAG, "MIC_PCM_BEGIN FRAMES=%u", static_cast<unsigned>(CAPTURE_FRAME_COUNT));
    for (size_t first_frame = 0; first_frame < CAPTURE_FRAME_COUNT; first_frame += 8) {
      char line[224];
      size_t used = 0;
      const size_t end_frame = std::min(first_frame + 8, CAPTURE_FRAME_COUNT);
      for (size_t frame = first_frame; frame < end_frame; frame++) {
        const int written = std::snprintf(line + used, sizeof(line) - used, "%u=%d,%d%s",
                                          static_cast<unsigned>(frame), capture_samples[frame * 2],
                                          capture_samples[frame * 2 + 1], frame + 1 == end_frame ? "" : " ");
        if (written <= 0 || static_cast<size_t>(written) >= sizeof(line) - used)
          break;
        used += static_cast<size_t>(written);
      }
      ESP_LOGI(TAG, "MIC_PCM %s", line);
    }
    ESP_LOGI(TAG, "MIC_PCM_END");
  }
}

void MicLevelMeter::dump_config() {
  ESP_LOGCONFIG(TAG, "Stereo microphone console meter:");
  ESP_LOGCONFIG(TAG, "  Update interval: %" PRIu32 " ms", this->get_update_interval());
}

}  // namespace esphome::mic_level_meter
