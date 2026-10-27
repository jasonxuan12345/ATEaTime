#pragma once

#include <cstddef>
#include <cstdint>
#include <istream>
#include <vector>

#include "deepyield_stdf/record_header.hpp"

namespace deepyield_stdf {

class BinaryReader {
 public:
  explicit BinaryReader(std::istream& input);

  bool read_header(RecordHeader& header);
  bool skip(std::size_t bytes);
  std::vector<std::uint8_t> read_bytes(std::size_t bytes);

 private:
  std::istream& input_;
};

}  // namespace deepyield_stdf
