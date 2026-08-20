#pragma once

#include "esphome/components/microphone/microphone.h"
#include "esphome/core/component.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

#ifdef USE_ESP32
#include <freertos/FreeRTOS.h>
#endif

namespace esphome::mic_level_meter {

class MicLevelMeter : public PollingComponent {
 public:
  void set_microphone(microphone::Microphone *microphone) { this->microphone_ = microphone; }

  void setup() override;
  void update() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

 protected:
  static constexpr size_t CAPTURE_FRAME_COUNT = 256;
  static constexpr size_t CAPTURE_SAMPLE_COUNT = CAPTURE_FRAME_COUNT * 2;

  void process_audio_(const std::vector<uint8_t> &data);

  microphone::Microphone *microphone_{nullptr};

#ifdef USE_ESP32
  portMUX_TYPE data_lock_ = portMUX_INITIALIZER_UNLOCKED;
#endif
  int64_t sum_[2]{0, 0};
  uint64_t square_sum_[2]{0, 0};
  uint32_t sample_count_[2]{0, 0};
  uint16_t peak_[2]{0, 0};
  std::array<int16_t, CAPTURE_SAMPLE_COUNT> capture_samples_{};
  size_t capture_sample_count_{0};
  uint8_t capture_delay_updates_{0};
  bool capture_ready_{false};
};

}  // namespace esphome::mic_level_meter
