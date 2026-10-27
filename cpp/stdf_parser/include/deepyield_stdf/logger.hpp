#pragma once
/// Lightweight buffered logger for STDF parser.
///
/// Replaces raw std::cerr / std::endl with leveled, throttled output.
/// Phase 1 of the DeepYield STDF parser performance optimization plan.
///
/// Default levels:
///   Release: Warn
///   Debug:   Info
///   --verbose: Info
///   --debug-trace: Trace

#include <atomic>
#include <fstream>
#include <string>
#include <string_view>

namespace deepyield_stdf {

enum class LogLevel {
    Error = 0,
    Warn  = 1,
    Info  = 2,
    Debug = 3,
    Trace = 4
};

class Logger {
public:
    static Logger& instance();

    /// Set the minimum level that will be emitted.
    void set_level(LogLevel level);
    LogLevel level() const;

    /// Check if a given level is currently enabled.
    bool enabled(LogLevel level) const;

    /// Write a message at the given level.  Does NOT append a newline;
    /// callers should include \n in the message when needed.
    void log(LogLevel level, std::string_view msg);

    /// Convenience methods.
    void error(std::string_view msg)   { log(LogLevel::Error, msg); }
    void warn(std::string_view msg)    { log(LogLevel::Warn,  msg); }
    void info(std::string_view msg)    { log(LogLevel::Info,  msg); }
    void debug(std::string_view msg)   { log(LogLevel::Debug, msg); }
    void trace(std::string_view msg)   { log(LogLevel::Trace, msg); }

    /// Flush the output stream (called at exit or on critical errors).
    void flush();

private:
    Logger();
    ~Logger();
    Logger(const Logger&) = delete;
    Logger& operator=(const Logger&) = delete;

    std::atomic<LogLevel> level_{LogLevel::Warn};
    std::ofstream trace_file_;
    bool trace_enabled_ = false;
};

// Convenience macros — zero-cost when level is disabled
#define DY_LOG_ERROR(msg) do { auto& _dy_log = ::deepyield_stdf::Logger::instance(); if (_dy_log.enabled(::deepyield_stdf::LogLevel::Error)) _dy_log.error(msg); } while(0)
#define DY_LOG_WARN(msg)  do { auto& _dy_log = ::deepyield_stdf::Logger::instance(); if (_dy_log.enabled(::deepyield_stdf::LogLevel::Warn))  _dy_log.warn(msg);  } while(0)
#define DY_LOG_INFO(msg)  do { auto& _dy_log = ::deepyield_stdf::Logger::instance(); if (_dy_log.enabled(::deepyield_stdf::LogLevel::Info))  _dy_log.info(msg);  } while(0)
#define DY_LOG_DEBUG(msg) do { auto& _dy_log = ::deepyield_stdf::Logger::instance(); if (_dy_log.enabled(::deepyield_stdf::LogLevel::Debug)) _dy_log.debug(msg); } while(0)
#define DY_LOG_TRACE(msg) do { auto& _dy_log = ::deepyield_stdf::Logger::instance(); if (_dy_log.enabled(::deepyield_stdf::LogLevel::Trace)) _dy_log.trace(msg); } while(0)

// Legacy-compatible buffered trace (replaces the old global g_trace with
// per-line flush).  Default OFF; enable with --debug-trace or
// DEEPYIELD_TRACE=1.
namespace trace {

void open_trace(const char* path = "stdf2parquet_crash_trace.log");
void close_trace();
void write_trace(const std::string& msg);
void flush_trace();
void set_trace_enabled(bool enabled);
bool is_trace_enabled();

}  // namespace trace

}  // namespace deepyield_stdf
