// Compact, deterministic SHA-256 (FIPS 180-4).
// Used for the frozen Test Item key / bucket digest algorithms shared with
// the Python reference implementation.
#pragma once

#include <array>
#include <cstdint>
#include <cstring>
#include <string>

namespace deepyield_stdf {

class Sha256 {
 public:
  Sha256() { reset(); }

  void reset() {
    state_[0] = 0x6a09e667;
    state_[1] = 0xbb67ae85;
    state_[2] = 0x3c6ef372;
    state_[3] = 0xa54ff53a;
    state_[4] = 0x510e527f;
    state_[5] = 0x9b05688c;
    state_[6] = 0x1f83d9ab;
    state_[7] = 0x5be0cd19;
    count_[0] = 0;
    count_[1] = 0;
  }

  void update(const void* data, std::size_t length) {
    const auto* bytes = static_cast<const std::uint8_t*>(data);
    std::size_t index = (count_[0] >> 3) & 63;
    if ((count_[0] += static_cast<std::uint32_t>(length) << 3) <
        (static_cast<std::uint32_t>(length) << 3)) {
      ++count_[1];
    }
    count_[1] += static_cast<std::uint32_t>(length >> 29);
    std::size_t i = 0;
    std::size_t first = 64 - index;
    if (length >= first) {
      std::memcpy(buffer_.data() + index, bytes, first);
      transform(buffer_.data());
      for (i = first; i + 63 < length; i += 64) transform(bytes + i);
      index = 0;
    }
    std::memcpy(buffer_.data() + index, bytes + i, length - i);
  }

  std::array<std::uint8_t, 32> digest() {
    std::uint8_t final_count[8];
    for (int i = 0; i < 8; ++i) {
      final_count[i] = static_cast<std::uint8_t>((count_[i >= 4 ? 0 : 1] >> ((3 - (i & 3)) * 8)) & 0xff);
    }
    std::uint8_t pad = 0x80;
    update(&pad, 1);
    pad = 0;
    while ((count_[0] & 504) != 448) update(&pad, 1);
    update(final_count, 8);
    std::array<std::uint8_t, 32> result{};
    for (int i = 0; i < 8; ++i) {
      result[i * 4] = static_cast<std::uint8_t>((state_[i] >> 24) & 0xff);
      result[i * 4 + 1] = static_cast<std::uint8_t>((state_[i] >> 16) & 0xff);
      result[i * 4 + 2] = static_cast<std::uint8_t>((state_[i] >> 8) & 0xff);
      result[i * 4 + 3] = static_cast<std::uint8_t>(state_[i] & 0xff);
    }
    return result;
  }

  std::string hex_digest_current() {
    const auto bytes = digest();
    static const char* hex = "0123456789abcdef";
    std::string out;
    out.reserve(64);
    for (const auto byte : bytes) {
      out.push_back(hex[(byte >> 4) & 0x0f]);
      out.push_back(hex[byte & 0x0f]);
    }
    return out;
  }

  static std::string hex_digest(const std::string& input) {
    Sha256 hasher;
    hasher.update(input.data(), input.size());
    auto bytes = hasher.digest();
    static const char* hex = "0123456789abcdef";
    std::string out;
    out.reserve(64);
    for (auto byte : bytes) {
      out.push_back(hex[(byte >> 4) & 0x0f]);
      out.push_back(hex[byte & 0x0f]);
    }
    return out;
  }

  static std::array<std::uint8_t, 32> raw_digest(const std::string& input) {
    Sha256 hasher;
    hasher.update(input.data(), input.size());
    return hasher.digest();
  }

 private:
  void transform(const std::uint8_t* block) {
    static const std::uint32_t k[] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
    std::uint32_t w[64];
    for (int i = 0; i < 16; ++i) {
      w[i] = (static_cast<std::uint32_t>(block[i * 4]) << 24) |
             (static_cast<std::uint32_t>(block[i * 4 + 1]) << 16) |
             (static_cast<std::uint32_t>(block[i * 4 + 2]) << 8) |
             static_cast<std::uint32_t>(block[i * 4 + 3]);
    }
    for (int i = 16; i < 64; ++i) {
      std::uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
      std::uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    std::uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
    std::uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
    for (int i = 0; i < 64; ++i) {
      std::uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      std::uint32_t ch = (e & f) ^ (~e & g);
      std::uint32_t temp1 = h + s1 + ch + k[i] + w[i];
      std::uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
      std::uint32_t temp2 = s0 + maj;
      h = g; g = f; f = e; e = d + temp1;
      d = c; c = b; b = a; a = temp1 + temp2;
    }
    state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
    state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
  }

  static std::uint32_t rotr(std::uint32_t value, int bits) {
    return (value >> bits) | (value << (32 - bits));
  }

  std::array<std::uint32_t, 8> state_{};
  std::array<std::uint8_t, 64> buffer_{};
  std::uint32_t count_[2] = {0, 0};
};

// Frozen Test Item identity payload and digest (contract_v1.json algorithms).
inline std::string test_item_payload(const std::string& record_type,
                                     std::uint32_t test_num,
                                     const std::string& result_index_text) {
  std::string payload = "test-item-v1\x1f";
  payload += record_type;
  payload += "\x1f";
  payload += std::to_string(test_num);
  payload += "\x1f";
  payload += result_index_text;
  return payload;
}

inline std::string test_item_key_hex(const std::string& payload) {
  return "tik_" + Sha256::hex_digest(payload).substr(0, 32);
}

inline std::array<std::uint8_t, 16> test_item_key_raw(const std::string& payload) {
  auto digest = Sha256::raw_digest(payload);
  std::array<std::uint8_t, 16> result{};
  std::memcpy(result.data(), digest.data(), 16);
  return result;
}

inline std::uint64_t bucket_hash_prefix(const std::string& payload) {
  auto digest = Sha256::raw_digest(payload);
  std::uint64_t value = 0;
  for (int i = 0; i < 8; ++i) {
    value = (value << 8) | digest[static_cast<std::size_t>(i)];
  }
  return value;
}

}  // namespace deepyield_stdf
