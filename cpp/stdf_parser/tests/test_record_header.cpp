#include <cassert>
#include <sstream>

#include "deepyield_stdf/binary_reader.hpp"

int main() {
  std::string bytes;
  bytes.push_back(static_cast<char>(0x34));
  bytes.push_back(static_cast<char>(0x12));
  bytes.push_back(static_cast<char>(15));
  bytes.push_back(static_cast<char>(10));
  std::istringstream input(bytes);
  deepyield_stdf::BinaryReader reader(input);
  deepyield_stdf::RecordHeader header;
  assert(reader.read_header(header));
  assert(header.length == 0x1234);
  assert(header.type == 15);
  assert(header.sub == 10);
  assert(header.key() == "15:10");
  return 0;
}
