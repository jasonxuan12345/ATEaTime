#include "deepyield_stdf/binary_reader.hpp"

#include <algorithm>
#include <limits>

namespace deepyield_stdf {

BinaryReader::BinaryReader(std::istream& input) : input_(input) {}

bool BinaryReader::read_header(RecordHeader& header) {
  unsigned char raw[4] = {0, 0, 0, 0};
  input_.read(reinterpret_cast<char*>(raw), 4);
  if (input_.gcount() == 0 && input_.eof()) {
    return false;
  }
  if (input_.gcount() != 4) {
    return false;
  }
  header.length = static_cast<std::uint16_t>(raw[0]) |
                  (static_cast<std::uint16_t>(raw[1]) << 8);
  header.type = raw[2];
  header.sub = raw[3];
  return true;
}

bool BinaryReader::skip(std::size_t bytes) {
  if (bytes > static_cast<std::size_t>(std::numeric_limits<std::streamoff>::max())) {
    return false;
  }
  input_.ignore(static_cast<std::streamsize>(bytes));
  return static_cast<std::size_t>(input_.gcount()) == bytes;
}

std::vector<std::uint8_t> BinaryReader::read_bytes(std::size_t bytes) {
  std::vector<std::uint8_t> buffer(bytes);
  input_.read(reinterpret_cast<char*>(buffer.data()), static_cast<std::streamsize>(bytes));
  buffer.resize(static_cast<std::size_t>(input_.gcount()));
  return buffer;
}

}  // namespace deepyield_stdf
