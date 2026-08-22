#pragma once

#include "esphome/components/microphone/microphone.h"
#include "esphome/components/socket/socket.h"
#include "esphome/core/component.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#ifdef USE_ESP32
#include <freertos/FreeRTOS.h>
#endif

namespace esphome::wifi_pcm_streamer {

class WifiPcmStreamer : public Component {
 public:
  void set_microphone(microphone::Microphone *microphone) { this->microphone_ = microphone; }
  void set_host(const std::string &host) { this->host_ = host; }
  void set_port(uint16_t port) { this->port_ = port; }

  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

 protected:
  static constexpr size_t PCM_BUFFER_SIZE = 32768;
  static constexpr size_t SEND_CHUNK_SIZE = 2048;
  static constexpr uint32_t RECONNECT_INTERVAL_MS = 1000;
  static constexpr uint32_t CONNECT_TIMEOUT_MS = 5000;

  void process_audio_(const std::vector<uint8_t> &data);
  void begin_connect_();
  void poll_connect_();
  void mark_connected_();
  void disconnect_();
  void send_pending_();

  microphone::Microphone *microphone_{nullptr};
  std::string host_;
  uint16_t port_{10300};
  std::unique_ptr<socket::Socket> socket_;
  bool connecting_{false};
  volatile bool connected_{false};
  uint32_t connect_started_ms_{0};
  uint32_t last_connect_attempt_ms_{0};

#ifdef USE_ESP32
  portMUX_TYPE data_lock_ = portMUX_INITIALIZER_UNLOCKED;
#endif
  std::array<uint8_t, PCM_BUFFER_SIZE> pcm_buffer_{};
  size_t pcm_head_{0};
  size_t pcm_tail_{0};
  size_t pcm_size_{0};
};

}  // namespace esphome::wifi_pcm_streamer
