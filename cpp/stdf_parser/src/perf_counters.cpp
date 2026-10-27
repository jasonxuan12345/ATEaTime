/// Implementation of the performance counters system.
/// Phase 0 of the DeepYield STDF parser performance optimization plan.

#include "deepyield_stdf/perf_counters.hpp"
#include <cstdlib>
#include <iomanip>
#include <sstream>

namespace deepyield_stdf {

namespace {

// Global perf counters singleton
ParserPerfCounters g_perf_counters;

// Whether perf logging is enabled
std::atomic<bool> g_perf_enabled{false};

// Check environment variable once on first access
bool check_env_perf() {
    static bool checked = false;
    static bool env_set = false;
    if (!checked) {
        const char* env = std::getenv("DEEPYIELD_PERF");
        env_set = (env && (std::string(env) == "1" || std::string(env) == "true"));
        checked = true;
    }
    return env_set;
}

}  // namespace

ParserPerfCounters& g_perf() {
    return g_perf_counters;
}

bool perf_log_enabled() {
    return g_perf_enabled.load(std::memory_order_relaxed) || check_env_perf();
}

void set_perf_log_enabled(bool enabled) {
    g_perf_enabled.store(enabled, std::memory_order_relaxed);
}

void write_perf_log(std::ostream& out, const ParserPerfCounters& c) {
    out << "{\n";
    out << "  \"parser_perf\": {\n";

    // Record counts
    out << "    \"records\": {\n";
    out << "      \"ptr_count\": " << c.ptr_count.load() << ",\n";
    out << "      \"mpr_count\": " << c.mpr_count.load() << ",\n";
    out << "      \"prr_count\": " << c.prr_count.load() << ",\n";
    out << "      \"ftr_count\": " << c.ftr_count.load() << ",\n";
    out << "      \"total_records\": " << c.total_records.load() << "\n";
    out << "    },\n";

    // Column name cache metrics
    out << "    \"column_cache\": {\n";
    out << "      \"name_calls\": " << c.test_column_name_calls.load() << ",\n";
    out << "      \"cache_hits\": " << c.test_column_cache_hits.load() << ",\n";
    out << "      \"cache_misses\": " << c.test_column_cache_misses.load() << "\n";
    out << "    },\n";

    // Key string metrics
    out << "    \"key_strings\": {\n";
    out << "      \"context_key_builds\": " << c.context_key_string_builds.load() << ",\n";
    out << "      \"attempt_key_builds\": " << c.attempt_key_string_builds.load() << "\n";
    out << "    },\n";

    // Arrow append metrics
    out << "    \"arrow_append\": {\n";
    out << "      \"append_cell_calls\": " << c.append_cell_calls.load() << ",\n";
    out << "      \"dynamic_cast_calls\": " << c.dynamic_cast_calls.load() << ",\n";
    out << "      \"row_copy_count\": " << c.row_copy_count.load() << ",\n";
    out << "      \"row_move_count\": " << c.row_move_count.load() << "\n";
    out << "    },\n";

    // Logging / trace metrics
    out << "    \"logging\": {\n";
    out << "      \"cerr_log_count\": " << c.cerr_log_count.load() << ",\n";
    out << "      \"trace_write_count\": " << c.trace_write_count.load() << ",\n";
    out << "      \"trace_flush_count\": " << c.trace_flush_count.load() << "\n";
    out << "    },\n";

    // Batch metrics
    out << "    \"batch\": {\n";
    out << "      \"batch_flush_count\": " << c.batch_flush_count.load() << ",\n";
    out << "      \"max_batch_rows\": " << c.max_batch_rows.load() << ",\n";
    out << "      \"max_batch_memory_estimate\": " << c.max_batch_memory_estimate.load() << "\n";
    out << "    }\n";

    out << "  }\n";
    out << "}\n";
    out.flush();
}

}  // namespace deepyield_stdf
