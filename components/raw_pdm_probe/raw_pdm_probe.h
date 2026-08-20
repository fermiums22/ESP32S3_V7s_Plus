#pragma once

#include "esphome/core/component.h"

#include <array>
#include <cstdint>

#ifdef USE_ESP32
#include <driver/i2s_pdm.h>
#endif

namespace esphome::raw_pdm_probe {

class RawPdmProbe : public Component {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

 protected:
  static constexpr size_t CAPTURE_BYTES = 4096;

  void start_();
  void capture_();

#ifdef USE_ESP32
  i2s_chan_handle_t rx_handle_{nullptr};
#endif
  std::array<uint8_t, CAPTURE_BYTES> data_{};
};

}  // namespace esphome::raw_pdm_probe
