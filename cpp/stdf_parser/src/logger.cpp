/// Implementation of the buffered logger.
/// Phase 1 of the DeepYield STDF parser performance optimization plan.

#include "deepyield_stdf/logger.hpp"
#include "deepyield_stdf/perf_counters.hpp"
#include <iostream>

#ifdef _DEBUG
#define DY_DEFAULT_LEVEL LogLevel::Info
#else
#define DY_DEFAULT_LEVEL LogLevel::Warn
#endif

namespace deepyield_stdf {

Logger& Logger::instance() {
    static Logger logger;
    return logger;
}

Logger::Logger() {
    level_.store(DY_DEFAULT_LEVEL, std::memory_order_relaxed);
}

Logger::~Logger() {
    flush();
    if (trace_file_.is_open()) {
        trace_file_ << "--- TRACE END ---\n";
        trace_file_.close();
    }
}

void Logger::set_level(LogLevel level) {
    level_.store(level, std::memory_order_relaxed);
}

LogLevel Logger::level() const {
    return level_.load(std::memory_order_relaxed);
}

bool Logger::enabled(LogLevel level) const {
    return static_cast<int>(level) <= static_cast<int>(level_.load(std::memory_order_relaxed));
}

void Logger::log(LogLevel level, std::string_view msg) {
    if (!enabled(level)) return;

    // Route Error/Warn to stderr, Info/Debug/Trace to stderr as well
    // (they are all diagnostic; JSON progress goes to stdout separately).
    // Use '\n' NOT std::endl to avoid implicit flush.
    std::cerr << msg;
    if (!msg.empty() && msg.back() != '\n') {
        std::cerr << '\n';
    }

    // If trace is enabled, also write to trace file (buffered)
    if (trace_enabled_ && trace_file_.is_open()) {
        trace_file_ << msg;
        if (!msg.empty() && msg.back() != '\n') {
            trace_file_ << '\n';
        }
    }
}

void Logger::flush() {
    std::cerr.flush();
    if (trace_file_.is_open()) {
        trace_file_.flush();
    }
}

// Legacy-compatible trace functions, now routed through Logger.
// These are called from parquet_writer.cpp and main.cpp.
namespace trace {

static std::ofstream g_legacy_trace;
static bool g_legacy_trace_enabled = false;

void open_trace(const char* path) {
    if (!g_legacy_trace_enabled) return;
    if (g_legacy_trace.is_open()) return;
    g_legacy_trace.open(path, std::ios::out | std::ios::app);
    if (g_legacy_trace.is_open()) {
        g_legacy_trace << "--- TRACE BEGIN ---\n";
    }
}

void close_trace() {
    if (g_legacy_trace.is_open()) {
        g_legacy_trace << "--- TRACE END ---\n";
        g_legacy_trace.flush();
        g_legacy_trace.close();
    }
}

void write_trace(const std::string& msg) {
    if (!g_legacy_trace_enabled || !g_legacy_trace.is_open()) return;
    g_legacy_trace << msg << "\n";
    ++g_perf().trace_write_count;
    // Buffered: only flush periodically (every 4096 lines or on close)
    static int line_count = 0;
    if (++line_count >= 4096) {
        g_legacy_trace.flush();
        ++g_perf().trace_flush_count;
        line_count = 0;
    }
}

void flush_trace() {
    if (g_legacy_trace.is_open()) {
        g_legacy_trace.flush();
        ++g_perf().trace_flush_count;
    }
}

void set_trace_enabled(bool enabled) {
    g_legacy_trace_enabled = enabled;
}

bool is_trace_enabled() {
    return g_legacy_trace_enabled;
}

}  // namespace trace

}  // namespace deepyield_stdf
