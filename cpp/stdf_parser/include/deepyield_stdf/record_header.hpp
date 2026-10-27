#pragma once

#include <cstdint>
#include <string>

namespace deepyield_stdf {

struct RecordHeader {
  std::uint16_t length = 0;
  std::uint8_t type = 0;
  std::uint8_t sub = 0;

  std::string key() const {
    return std::to_string(static_cast<unsigned int>(type)) + ":" +
           std::to_string(static_cast<unsigned int>(sub));
  }
};

}  // namespace deepyield_stdf
