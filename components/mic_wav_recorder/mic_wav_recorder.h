#pragma once

#include "esphome/components/microphone/microphone.h"
#include "esphome/core/component.h"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace esphome::mic_wav_recorder {

class MicWavRecorder : public Component {
 public:
  void set_microphone(microphone::Microphone *microphone) { this->microphone_ = microphone; }

  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

 protected:
  static constexpr uint32_t SAMPLE_RATE = 16000;
  static constexpr uint8_t CHANNEL_COUNT = 2;
  static constexpr uint8_t BYTES_PER_SAMPLE = 2;
  static constexpr uint32_t RECORD_SECONDS = 10;
  static constexpr size_t RECORD_BYTES = SAMPLE_RATE * CHANNEL_COUNT * BYTES_PER_SAMPLE * RECORD_SECONDS;
  // Keep the complete log line below logger's 512-byte TX buffer.
  static constexpr size_t DUMP_CHUNK_BYTES = 192;
  static constexpr size_t DUMP_CHUNKS_PER_LOOP = 2;

  void process_audio_(const std::vector<uint8_t> &data);

  microphone::Microphone *microphone_{nullptr};
  uint8_t *recording_{nullptr};
  size_t captured_bytes_{0};
  size_t dump_offset_{0};
  std::atomic<bool> recording_started_{false};
  std::atomic<bool> recording_ready_{false};
  bool recording_announced_{false};
  bool dump_started_{false};
  bool dump_finished_{false};
};

}  // namespace esphome::mic_wav_recorder
