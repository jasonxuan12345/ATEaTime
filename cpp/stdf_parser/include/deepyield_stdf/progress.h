#pragma once
/// JSONL progress reporter for the STDF parser.
///
/// Emits machine-readable JSONL lines to stdout that the Python
/// ProgressCollector reads to update SQLite progress fields.
///
/// Step 9 of the DeepYield import reliability plan.

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <ostream>
#include <sstream>
#include <string>

namespace deepyield_stdf {

// ---------------------------------------------------------------------------
// ProgressEmitter  –  thread-safe JSONL emitter
// ---------------------------------------------------------------------------

class ProgressEmitter {
  public:
    explicit ProgressEmitter(std::ostream& out = std::cout)
        : out_(&out) {}

    /// Set total compressed bytes (called once before parse starts).
    void set_compressed_total(std::uint64_t total) {
        compressed_total_.store(total, std::memory_order_relaxed);
    }

    /// Update compressed-bytes-read counter (called from bz2 streambuf).
    void add_compressed_read(std::uint64_t delta) {
        compressed_read_.fetch_add(delta, std::memory_order_relaxed);
    }

    /// Called from main loop when a progress milestone is reached.
    void emit_parse_progress(const char* stage,
                             std::uint64_t records_seen,
                             std::uint64_t ptr_count,
                             std::uint64_t prr_count,
                             std::uint64_t ftr_count,
                             std::uint64_t mpr_count);

    /// Called before / after each table write in close_impl.
    void emit_stage(const char* stage);
    void emit_stage(const char* stage, const char* detail);

    /// Called when writer close completes successfully.
    void emit_completed(std::uint64_t output_rows);

    /// Called when an unrecoverable error occurs.
    void emit_error(const char* stage, const char* message);

    /// Wire the emitter to use external bz2 progress counters.
    /// Call this before parsing starts so that emit_parse_progress()
    /// reads live compressed-byte values from the decompression layer.
    void set_compressed_counters(
        std::shared_ptr<std::atomic<std::uint64_t>> read_counter,
        std::shared_ptr<std::atomic<std::uint64_t>> total_counter) {
        if (read_counter) compressed_read_ext_ = std::move(read_counter);
        if (total_counter) compressed_total_ext_ = std::move(total_counter);
    }

  private:
    void emit_json(const char* json);
    double calc_percent(double fraction, double range_start, double range_end);

    std::ostream* out_;
    // External counters (set from bz2 layer via set_compressed_counters)
    std::shared_ptr<std::atomic<std::uint64_t>> compressed_read_ext_;
    std::shared_ptr<std::atomic<std::uint64_t>> compressed_total_ext_;
    // Fallback local counters (used when bz2 progress not wired)
    std::atomic<std::uint64_t> compressed_read_{0};
    std::atomic<std::uint64_t> compressed_total_{0};
    std::chrono::steady_clock::time_point parse_start_;
    bool parse_started_ = false;
};

// ---------------------------------------------------------------------------
// Progress callback type
// ---------------------------------------------------------------------------

using ProgressCallback = std::function<void(ProgressEmitter&)>;

}  // namespace deepyield_stdf
