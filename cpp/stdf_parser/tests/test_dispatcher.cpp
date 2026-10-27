#include <cassert>
#include <sstream>

#include "deepyield_stdf/binary_reader.hpp"
#include "deepyield_stdf/record_dispatcher.hpp"

namespace {

void append_record(std::string& bytes, unsigned short len, unsigned char type, unsigned char sub) {
  bytes.push_back(static_cast<char>(len & 0xff));
  bytes.push_back(static_cast<char>((len >> 8) & 0xff));
  bytes.push_back(static_cast<char>(type));
  bytes.push_back(static_cast<char>(sub));
  for (unsigned short i = 0; i < len; ++i) {
    bytes.push_back('\0');
  }
}

}  // namespace

int main() {
  std::string bytes;
  append_record(bytes, 2, 15, 10);
  append_record(bytes, 3, 99, 99);
  std::istringstream input(bytes);
  deepyield_stdf::BinaryReader reader(input);
  deepyield_stdf::RecordDispatcher dispatcher;
  deepyield_stdf::DispatchStats stats = dispatcher.scan(reader);
  assert(stats.records == 2);
  assert(stats.skipped_unknown == 1);
  assert(stats.by_record["15:10"] == 1);
  assert(stats.by_record["99:99"] == 1);
  return 0;
}
