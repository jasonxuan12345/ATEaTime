#pragma once

#include <atomic>
#include <cstdint>
#include <istream>
#include <memory>
#include <string>

namespace deepyield_stdf {

/// Shared progress counters exposed by the BZ2 decompression layer.
/// The caller can poll these atomics to obtain real-time progress.
struct Bz2Progress {
    std::shared_ptr<std::atomic<std::uint64_t>> compressed_bytes_read;
    std::shared_ptr<std::atomic<std::uint64_t>> compressed_bytes_total;
};

/// Opens a .bz2 file and returns a std::istream that decompresses on the fly.
///
/// Uses libbz2 internally.  No temporary file is written to disk — the
/// caller reads decompressed STDF bytes directly from the returned stream.
///
/// If *progress* is not null, the pointed-to struct is populated with
/// shared atomic counters that are updated during decompression.
///
/// Throws std::runtime_error if the file cannot be opened or is not valid
/// bzip2 data.
std::unique_ptr<std::istream> open_bz2_input_stream(
    const std::string& path,
    Bz2Progress* progress = nullptr);

}  // namespace deepyield_stdf
