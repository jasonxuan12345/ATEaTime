#pragma once
/// Performance counters for STDF parser optimization tracking.
///
/// Counters are incremented in hot paths but are designed to be
/// low-overhead (atomic increments, no string ops in hot path).
/// Output only when --perf-log is passed or DEEPYIELD_PERF=1.
///
/// Phase 0 of the DeepYield STDF parser performance optimization plan.

#include <atomic>
#include <cstdint>
#include <iostream>
#include <string>

namespace deepyield_stdf {

struct ParserPerfCounters {
    // Record counts
    std::atomic<std::uint64_t> ptr_count{0};
    std::atomic<std::uint64_t> mpr_count{0};
    std::atomic<std::uint64_t> prr_count{0};
    std::atomic<std::uint64_t> ftr_count{0};
    std::atomic<std::uint64_t> total_records{0};

    // Column name generation
    std::atomic<std::uint64_t> test_column_name_calls{0};
    std::atomic<std::uint64_t> test_column_cache_hits{0};
    std::atomic<std::uint64_t> test_column_cache_misses{0};

    // Key string building
    std::atomic<std::uint64_t> context_key_string_builds{0};
    std::atomic<std::uint64_t> attempt_key_string_builds{0};

    // Arrow append
    std::atomic<std::uint64_t> append_cell_calls{0};
    std::atomic<std::uint64_t> dynamic_cast_calls{0};
    std::atomic<std::uint64_t> row_copy_count{0};
    std::atomic<std::uint64_t> row_move_count{0};

    // Logging / trace
    std::atomic<std::uint64_t> cerr_log_count{0};
    std::atomic<std::uint64_t> trace_write_count{0};
    std::atomic<std::uint64_t> trace_flush_count{0};

    // Batch metrics
    std::atomic<std::uint64_t> batch_flush_count{0};
    std::atomic<std::uint64_t> max_batch_rows{0};
    std::atomic<std::uint64_t> max_batch_memory_estimate{0};

    /// Reset all counters to zero.
    void reset() {
        ptr_count = 0;
        mpr_count = 0;
        prr_count = 0;
        ftr_count = 0;
        total_records = 0;
        test_column_name_calls = 0;
        test_column_cache_hits = 0;
        test_column_cache_misses = 0;
        context_key_string_builds = 0;
        attempt_key_string_builds = 0;
        append_cell_calls = 0;
        dynamic_cast_calls = 0;
        row_copy_count = 0;
        row_move_count = 0;
        cerr_log_count = 0;
        trace_write_count = 0;
        trace_flush_count = 0;
        batch_flush_count = 0;
        max_batch_rows = 0;
        max_batch_memory_estimate = 0;
    }
};

/// Global perf counters instance (defined in perf_counters.cpp or inline).
/// Access via g_perf() to ensure initialization before use.
ParserPerfCounters& g_perf();

/// Write perf counters as JSON to the given stream.
/// Called at exit when --perf-log is enabled.
void write_perf_log(std::ostream& out, const ParserPerfCounters& c);

/// Check if perf logging is enabled (via --perf-log or DEEPYIELD_PERF=1).
bool perf_log_enabled();

/// Enable or disable perf logging. Called from CLI arg parsing.
void set_perf_log_enabled(bool enabled);

}  // namespace deepyield_stdf
