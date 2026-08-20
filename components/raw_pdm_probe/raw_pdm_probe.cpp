#include "raw_pdm_probe.h"

#include "esphome/core/log.h"

#include <algorithm>
#include <cstdio>

namespace esphome::raw_pdm_probe {

static const char *const TAG = "raw_pdm_probe";

void RawPdmProbe::setup() {
  // GPIO12 power switch is already on; give the microphone LDO time to settle.
  this->set_timeout(500, [this]() { this->start_(); });
}

void RawPdmProbe::start_() {
#ifdef USE_ESP32
  i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  channel_config.dma_desc_num = 8;
  channel_config.dma_frame_num = 256;

  esp_err_t error = i2s_new_channel(&channel_config, nullptr, &this->rx_handle_);
  if (error != ESP_OK) {
    ESP_LOGE(TAG, "i2s_new_channel failed: %s", esp_err_to_name(error));
    this->mark_failed();
    return;
  }

  i2s_pdm_rx_config_t config = {
      .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(512000),
      .slot_cfg = I2S_PDM_RX_SLOT_RAW_FMT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
      .gpio_cfg =
          {
              .clk = GPIO_NUM_10,
              .din = GPIO_NUM_11,
              .invert_flags =
                  {
                      .clk_inv = false,
                  },
          },
  };
  config.slot_cfg.slot_mask = I2S_PDM_SLOT_BOTH;

  error = i2s_channel_init_pdm_rx_mode(this->rx_handle_, &config);
  if (error == ESP_OK)
    error = i2s_channel_enable(this->rx_handle_);
  if (error != ESP_OK) {
    ESP_LOGE(TAG, "Raw PDM initialization failed: %s", esp_err_to_name(error));
    this->mark_failed();
    return;
  }

  ESP_LOGI(TAG, "Raw PDM active: CLK=GPIO10 1.024MHz, DATA=GPIO11");
  this->set_interval("raw_capture", 3000, [this]() { this->capture_(); });
#endif
}

void RawPdmProbe::capture_() {
#ifdef USE_ESP32
  size_t bytes_read = 0;
  const esp_err_t error =
      i2s_channel_read(this->rx_handle_, this->data_.data(), this->data_.size(), &bytes_read, pdMS_TO_TICKS(100));
  if (error != ESP_OK || bytes_read == 0) {
    ESP_LOGW(TAG, "Raw PDM read failed: %s, bytes=%u", esp_err_to_name(error), static_cast<unsigned>(bytes_read));
    return;
  }

  uint32_t ones = 0;
  uint32_t transitions = 0;
  bool have_previous = false;
  bool previous = false;
  for (size_t index = 0; index < bytes_read; index++) {
    const uint8_t value = this->data_[index];
    ones += static_cast<uint32_t>(__builtin_popcount(static_cast<unsigned>(value)));
    for (uint8_t bit_index = 0; bit_index < 8; bit_index++) {
      const bool bit = (value >> bit_index) & 1U;
      if (have_previous && bit != previous)
        transitions++;
      previous = bit;
      have_previous = true;
    }
  }

  ESP_LOGI(TAG, "PDM_RAW_BEGIN BYTES=%u ONES=%" PRIu32 " TRANSITIONS=%" PRIu32,
           static_cast<unsigned>(bytes_read), ones, transitions);
  for (size_t offset = 0; offset < bytes_read; offset += 32) {
    char line[65];
    const size_t line_bytes = std::min<size_t>(32, bytes_read - offset);
    for (size_t index = 0; index < line_bytes; index++)
      std::snprintf(line + index * 2, sizeof(line) - index * 2, "%02X", this->data_[offset + index]);
    line[line_bytes * 2] = '\0';
    ESP_LOGI(TAG, "PDM_RAW %04X:%s", static_cast<unsigned>(offset), line);
  }
  ESP_LOGI(TAG, "PDM_RAW_END");
#endif
}

void RawPdmProbe::dump_config() {
  ESP_LOGCONFIG(TAG, "Raw PDM diagnostic probe:");
  ESP_LOGCONFIG(TAG, "  GPIO10: 1.024 MHz clock output");
  ESP_LOGCONFIG(TAG, "  GPIO11: raw PDM input");
}

}  // namespace esphome::raw_pdm_probe
