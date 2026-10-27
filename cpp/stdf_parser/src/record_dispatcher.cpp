#include "deepyield_stdf/record_dispatcher.hpp"

#include <set>

namespace deepyield_stdf {

bool RecordDispatcher::is_supported(const RecordHeader& header) const {
  static const std::set<std::string> supported = {
      "0:10",   // FAR
      "1:10",   // MIR
      "1:80",   // SDR
      "2:10",   // WIR
      "5:10",   // PIR
      "15:10",  // PTR
      "15:15",  // MPR
      "15:20",  // FTR
      "5:20",   // PRR
      "2:20",   // WRR
      "1:20",   // MRR
  };
  return supported.count(header.key()) > 0;
}

DispatchStats RecordDispatcher::scan(BinaryReader& reader) {
  DispatchStats stats;
  RecordHeader header;
  while (reader.read_header(header)) {
    stats.records += 1;
    stats.by_record[header.key()] += 1;
    if (!is_supported(header)) {
      stats.skipped_unknown += 1;
    }
    if (!reader.skip(header.length)) {
      break;
    }
  }
  return stats;
}

}  // namespace deepyield_stdf
