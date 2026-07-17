#pragma once

#include "esphome/components/speaker/speaker.h"
#include "esphome/components/audio/audio.h"
#include "esphome/core/component.h"

#include <atomic>
#include <cstdint>

namespace esphome::pcm_i2s_tx {

class PcmI2sTx : public Component, public speaker::Speaker {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  size_t play(const uint8_t *data, size_t length) override;
  size_t play(const uint8_t *data, size_t length, TickType_t ticks_to_wait) override;
  void start() override;
  void stop() override;
  void finish() override;
  bool has_buffered_data() const override;
  audio::AudioFile *get_local_audio_file();
  void set_test_audio(const uint8_t *data, size_t size) {
    this->test_audio_ = data;
    this->test_audio_size_ = size;
  }
  void trigger_test_audio() { this->test_audio_request_.fetch_add(1, std::memory_order_release); }
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

 protected:
  static void i2s_task_(void *arg);

  static constexpr uint32_t BUFFER_SIZE = 65536;
  uint8_t *buffer_{nullptr};
  const uint8_t *test_audio_{nullptr};
  size_t test_audio_size_{0};
  std::atomic<uint32_t> test_audio_request_{0};
  std::atomic<uint32_t> read_{0};
  std::atomic<uint32_t> write_{0};
  std::atomic<bool> streaming_{false};
  std::atomic<bool> finishing_{false};
  std::atomic<uint32_t> network_bytes_{0};
  std::atomic<uint32_t> i2s_bytes_{0};
  std::atomic<uint32_t> i2s_nonzero_{0};
  std::atomic<uint32_t> underruns_{0};
  uint8_t *local_audio_data_{nullptr};
  audio::AudioFile local_audio_file_{nullptr, 0, audio::AudioFileType::MP3};
  uint32_t last_report_{0};
};

}  // namespace esphome::pcm_i2s_tx
