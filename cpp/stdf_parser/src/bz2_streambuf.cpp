#include "deepyield_stdf/bz2_streambuf.hpp"

#include <bzlib.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace deepyield_stdf {
namespace {

constexpr std::size_t kCompressedChunkSize   = 64 * 1024;   //  64 KB
constexpr std::size_t kDecompressedBufferSize = 256 * 1024;  // 256 KB

/// Custom std::streambuf that decompresses bzip2 data on the fly.
///
/// Uses the low-level BZ2_bzDecompressInit / BZ2_bzDecompress / BZ2_bzDecompressEnd
/// API to avoid CRT incompatibility when passing FILE* across DLL boundaries.
/// We handle compressed-data I/O ourselves via std::ifstream.
class Bz2DecompressStreambuf : public std::streambuf {
 public:
  explicit Bz2DecompressStreambuf(const std::string& path,
                                   Bz2Progress* progress = nullptr)
      : source_(path, std::ios::binary) {
    if (!source_) {
      throw std::runtime_error("Cannot open bzip2 input: " + path);
    }

    // Populate progress counters if requested
    if (progress) {
      compressed_read_ = std::make_shared<std::atomic<std::uint64_t>>(0);
      compressed_total_ = std::make_shared<std::atomic<std::uint64_t>>(0);
      // Best-effort file size; may be 0 for non-regular files
      std::error_code ec;
      auto sz = std::filesystem::file_size(path, ec);
      if (!ec) compressed_total_->store(static_cast<std::uint64_t>(sz));
      progress->compressed_bytes_read = compressed_read_;
      progress->compressed_bytes_total = compressed_total_;
    }

    // Initialise low-level decompressor
    std::memset(&strm_, 0, sizeof(strm_));
    int rc = BZ2_bzDecompressInit(&strm_, 0, 0);
    if (rc != BZ_OK) {
      throw std::runtime_error(
          "BZ2_bzDecompressInit failed for: " + path +
          " (rc=" + std::to_string(rc) + ")");
    }
    init_ok_ = true;

    // Allocate buffers
    compressed_.resize(kCompressedChunkSize);
    decompressed_.resize(kDecompressedBufferSize);

    // Mark get-area as empty so the first read triggers underflow()
    setg(nullptr, nullptr, nullptr);
  }

  ~Bz2DecompressStreambuf() override {
    if (init_ok_) {
      BZ2_bzDecompressEnd(&strm_);
      init_ok_ = false;
    }
  }

  Bz2DecompressStreambuf(const Bz2DecompressStreambuf&) = delete;
  Bz2DecompressStreambuf& operator=(const Bz2DecompressStreambuf&) = delete;

 protected:
  int underflow() override {
    if (gptr() < egptr()) {
      return traits_type::to_int_type(*gptr());
    }

    // Feed compressed data until we produce decompressed output
    while (true) {
      // If the decompressor needs more input, read a compressed chunk
      if (strm_.avail_in == 0) {
        source_.read(compressed_.data(),
                     static_cast<std::streamsize>(compressed_.size()));
        const auto bytes_read =
            static_cast<unsigned int>(source_.gcount());

        if (bytes_read == 0) {
          if (source_.eof()) {
            return traits_type::eof();
          }
          throw std::runtime_error("Error reading bzip2 source");
        }

        // Track compressed bytes read (Step 9 progress telemetry)
        if (compressed_read_) {
          compressed_read_->fetch_add(bytes_read, std::memory_order_relaxed);
        }

        strm_.next_in = compressed_.data();
        strm_.avail_in = bytes_read;
      }

      // Decompress
      strm_.next_out = decompressed_.data();
      strm_.avail_out = static_cast<unsigned int>(decompressed_.size());
      int rc = BZ2_bzDecompress(&strm_);

      if (rc == BZ_STREAM_END) {
        // Final chunk
        auto produced = decompressed_.size() - strm_.avail_out;
        if (produced > 0) {
          setg(decompressed_.data(),
               decompressed_.data(),
               decompressed_.data() + produced);
          return traits_type::to_int_type(*gptr());
        }
        return traits_type::eof();
      }

      if (rc == BZ_OK) {
        auto produced = decompressed_.size() - strm_.avail_out;
        if (produced > 0) {
          setg(decompressed_.data(),
               decompressed_.data(),
               decompressed_.data() + produced);
          return traits_type::to_int_type(*gptr());
        }
        // No output produced — need more input, continue loop
        continue;
      }

      // Any other return code is an error
      throw std::runtime_error(
          "BZ2_bzDecompress error: rc=" + std::to_string(rc) +
          " (0=OK, 4=STREAM_END, -1=SEQUENCE_ERROR, -4=DATA_ERROR, -5=MAGIC_ERROR)");
    }
  }

 private:
  std::ifstream source_;
  bz_stream strm_{};
  bool init_ok_ = false;
  std::vector<char> compressed_;
  std::vector<char> decompressed_;
  std::shared_ptr<std::atomic<std::uint64_t>> compressed_read_;
  std::shared_ptr<std::atomic<std::uint64_t>> compressed_total_;
};

// Thin adapter: the streambuf is used via its parent type std::streambuf,
// but we must keep the concrete Bz2DecompressStreambuf alive.
// Must be declared before open_bz2_input_stream.
struct Bz2DecompressStreambufWrapper : public std::istream {
  explicit Bz2DecompressStreambufWrapper(const std::string& path,
                                          Bz2Progress* progress)
      : std::istream(static_cast<std::streambuf*>(nullptr)),
        buf_(path, progress) {
    rdbuf(&buf_);
  }
 private:
  Bz2DecompressStreambuf buf_;
};

}  // namespace

std::unique_ptr<std::istream> open_bz2_input_stream(
    const std::string& path,
    Bz2Progress* progress) {
  return std::make_unique<Bz2DecompressStreambufWrapper>(
      path, progress);
}

}  // namespace deepyield_stdf
