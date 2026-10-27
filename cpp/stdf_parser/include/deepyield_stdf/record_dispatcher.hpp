#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>

#include "deepyield_stdf/binary_reader.hpp"

namespace deepyield_stdf {

struct DispatchStats {
  std::size_t records = 0;
  std::size_t skipped_unknown = 0;
  std::map<std::string, std::size_t> by_record;
};

class RecordDispatcher {
 public:
  DispatchStats scan(BinaryReader& reader);

 private:
  bool is_supported(const RecordHeader& header) const;
};

}  // namespace deepyield_stdf
