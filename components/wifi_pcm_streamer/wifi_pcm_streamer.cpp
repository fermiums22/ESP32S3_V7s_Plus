#include "wifi_pcm_streamer.h"

#include "esphome/components/network/util.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <algorithm>
#include <cerrno>
#include <cstring>

namespace esphome::wifi_pcm_streamer {

static const char *const TAG = "wifi_pcm_streamer";

void WifiPcmStreamer::setup() {
  if (this->microphone_ == nullptr) {
    ESP_LOGE(TAG, "Microphone is not configured");
    this->mark_failed();
    return;
  }

  this->microphone_->add_data_callback(
      [this](const std::vector<uint8_t> &data) { this->process_audio_(data); });

  // The microphone power switch is enabled during boot. Starting it here keeps
  // the network stream independent from diagnostic meter components.
  this->set_timeout(300, [this]() { this->microphone_->start(); });
}

void WifiPcmStreamer::process_audio_(const std::vector<uint8_t> &data) {
  if (!this->connected_ || data.size() < 4)
    return;

#ifdef USE_ESP32
  portENTER_CRITICAL(&this->data_lock_);
#endif
  // The source is 16-bit little-endian stereo PDM. Downmix it to the mono
  // 16 kHz PCM format consumed by Sokol's existing VAD/STT path.
  for (size_t offset = 0; offset + 3 < data.size(); offset += 4) {
    const int16_t left = static_cast<int16_t>(
        static_cast<uint16_t>(data[offset]) | (static_cast<uint16_t>(data[offset + 1]) << 8));
    const int16_t right = static_cast<int16_t>(
        static_cast<uint16_t>(data[offset + 2]) | (static_cast<uint16_t>(data[offset + 3]) << 8));
    const int16_t mono = static_cast<int16_t>((static_cast<int32_t>(left) + right) / 2);

    while (this->pcm_size_ + 2 > PCM_BUFFER_SIZE) {
      this->pcm_tail_ = (this->pcm_tail_ + 2) % PCM_BUFFER_SIZE;
      this->pcm_size_ -= 2;
    }
    this->pcm_buffer_[this->pcm_head_] = static_cast<uint8_t>(mono & 0xFF);
    this->pcm_head_ = (this->pcm_head_ + 1) % PCM_BUFFER_SIZE;
    this->pcm_buffer_[this->pcm_head_] = static_cast<uint8_t>((static_cast<uint16_t>(mono) >> 8) & 0xFF);
    this->pcm_head_ = (this->pcm_head_ + 1) % PCM_BUFFER_SIZE;
    this->pcm_size_ += 2;
  }
#ifdef USE_ESP32
  portEXIT_CRITICAL(&this->data_lock_);
#endif
}

void WifiPcmStreamer::begin_connect_() {
  this->last_connect_attempt_ms_ = millis();
  this->socket_ = socket::socket_ip(SOCK_STREAM, IPPROTO_TCP);
  if (!this->socket_) {
    ESP_LOGW(TAG, "Unable to create TCP socket");
    return;
  }
  if (this->socket_->setblocking(false) != 0) {
    ESP_LOGW(TAG, "Unable to set non-blocking TCP mode: errno %d", errno);
    this->disconnect_();
    return;
  }

  struct sockaddr_storage destination;
  const socklen_t destination_len = socket::set_sockaddr(
      reinterpret_cast<struct sockaddr *>(&destination), sizeof(destination), this->host_, this->port_);
  if (destination_len == 0) {
    ESP_LOGW(TAG, "Invalid PCM receiver address %s:%u", this->host_.c_str(), this->port_);
    this->disconnect_();
    return;
  }

  const int result = this->socket_->connect(
      reinterpret_cast<struct sockaddr *>(&destination), destination_len);
  if (result == 0) {
    this->mark_connected_();
    return;
  }
  if (errno == EINPROGRESS || errno == EWOULDBLOCK || errno == EAGAIN) {
    this->connecting_ = true;
    this->connect_started_ms_ = millis();
    return;
  }

  ESP_LOGW(TAG, "PCM TCP connection failed: errno %d", errno);
  this->disconnect_();
}

void WifiPcmStreamer::poll_connect_() {
  if (!this->socket_)
    return;
  if (millis() - this->connect_started_ms_ > CONNECT_TIMEOUT_MS) {
    ESP_LOGW(TAG, "PCM TCP connection timed out");
    this->disconnect_();
    return;
  }

  fd_set write_fds;
  FD_ZERO(&write_fds);
  FD_SET(this->socket_->get_fd(), &write_fds);
  struct timeval timeout = {0, 0};
  const int ready = select(this->socket_->get_fd() + 1, nullptr, &write_fds, nullptr, &timeout);
  if (ready <= 0 || !FD_ISSET(this->socket_->get_fd(), &write_fds))
    return;

  int error = 0;
  socklen_t error_len = sizeof(error);
  if (this->socket_->getsockopt(SOL_SOCKET, SO_ERROR, &error, &error_len) != 0 || error != 0) {
    ESP_LOGW(TAG, "PCM TCP connection failed: errno %d", error != 0 ? error : errno);
    this->disconnect_();
    return;
  }
  this->mark_connected_();
}

void WifiPcmStreamer::mark_connected_() {
  int enabled = 1;
  this->socket_->setsockopt(IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled));
  this->connecting_ = false;
  this->connected_ = true;
  ESP_LOGI(TAG, "Streaming microphone PCM to %s:%u over Wi-Fi", this->host_.c_str(), this->port_);
}

void WifiPcmStreamer::disconnect_() {
  this->connected_ = false;
  this->connecting_ = false;
  if (this->socket_) {
    this->socket_->close();
    this->socket_.reset();
  }
#ifdef USE_ESP32
  portENTER_CRITICAL(&this->data_lock_);
#endif
  this->pcm_head_ = 0;
  this->pcm_tail_ = 0;
  this->pcm_size_ = 0;
#ifdef USE_ESP32
  portEXIT_CRITICAL(&this->data_lock_);
#endif
}

void WifiPcmStreamer::send_pending_() {
  std::array<uint8_t, SEND_CHUNK_SIZE> chunk{};
  size_t chunk_size = 0;
#ifdef USE_ESP32
  portENTER_CRITICAL(&this->data_lock_);
#endif
  chunk_size = std::min(this->pcm_size_, chunk.size());
  for (size_t i = 0; i < chunk_size; i++)
    chunk[i] = this->pcm_buffer_[(this->pcm_tail_ + i) % PCM_BUFFER_SIZE];
#ifdef USE_ESP32
  portEXIT_CRITICAL(&this->data_lock_);
#endif
  if (chunk_size == 0)
    return;

  const ssize_t sent = this->socket_->write(chunk.data(), chunk_size);
  if (sent > 0) {
#ifdef USE_ESP32
    portENTER_CRITICAL(&this->data_lock_);
#endif
    const size_t consumed = std::min(static_cast<size_t>(sent), this->pcm_size_);
    this->pcm_tail_ = (this->pcm_tail_ + consumed) % PCM_BUFFER_SIZE;
    this->pcm_size_ -= consumed;
#ifdef USE_ESP32
    portEXIT_CRITICAL(&this->data_lock_);
#endif
    return;
  }
  if (sent < 0 && (errno == EWOULDBLOCK || errno == EAGAIN))
    return;

  ESP_LOGW(TAG, "PCM TCP stream disconnected: errno %d", errno);
  this->disconnect_();
}

void WifiPcmStreamer::loop() {
  if (!network::is_connected()) {
    if (this->connected_ || this->connecting_)
      this->disconnect_();
    return;
  }
  if (this->connecting_) {
    this->poll_connect_();
    return;
  }
  if (!this->connected_) {
    if (millis() - this->last_connect_attempt_ms_ >= RECONNECT_INTERVAL_MS)
      this->begin_connect_();
    return;
  }
  this->send_pending_();
}

void WifiPcmStreamer::dump_config() {
  ESP_LOGCONFIG(TAG, "Wi-Fi PCM streamer:");
  ESP_LOGCONFIG(TAG, "  Receiver: %s:%u", this->host_.c_str(), this->port_);
  ESP_LOGCONFIG(TAG, "  Format: 16 kHz, 16-bit, mono PCM");
}

}  // namespace esphome::wifi_pcm_streamer
