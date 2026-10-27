/// Implementation of the JSONL progress reporter.
/// See include/deepyield_stdf/progress.h for the public API.

#include "deepyield_stdf/progress.h"
#include <iomanip>
#include <sstream>

namespace deepyield_stdf {

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

namespace {

/// Write an integer as a JSON number (no decimal point, no scientific notation).
void write_json_int(std::ostream& os, std::uint64_t value) {
    os << value;
}

/// Write a double with one decimal place.
void write_json_double(std::ostream& os, double value) {
    // Avoid locale issues: format manually
    std::ostringstream tmp;
    tmp << std::fixed << std::setprecision(1) << value;
    std::string s = tmp.str();
    // Strip trailing ".0" if present and not ".0"
    if (s.size() > 2 && s.substr(s.size() - 2) == ".0") {
        os << s.substr(0, s.size() - 2);
    } else {
        os << s;
    }
}

/// Minimal JSON string escaping.
void write_json_string(std::ostream& os, const char* value) {
    os << '"';
    for (const char* p = value; *p; ++p) {
        switch (*p) {
            case '"':  os << "\\\""; break;
            case '\\': os << "\\\\"; break;
            case '\n': os << "\\n";  break;
            case '\r': os << "\\r";  break;
            case '\t': os << "\\t";  break;
            default:   os << *p;     break;
        }
    }
    os << '"';
}

}  // anonymous namespace

// ---------------------------------------------------------------------------
// ProgressEmitter implementation
// ---------------------------------------------------------------------------

void ProgressEmitter::emit_json(const char* json) {
    (*out_) << json << "\n";
    out_->flush();
}

double ProgressEmitter::calc_percent(double fraction, double range_start, double range_end) {
    double pct = range_start + fraction * (range_end - range_start);
    if (pct < 0.0) pct = 0.0;
    if (pct > 99.9) pct = 99.9;  // never reach 100 until completed
    return pct;
}

void ProgressEmitter::emit_parse_progress(
    const char* stage,
    std::uint64_t records_seen,
    std::uint64_t ptr_count,
    std::uint64_t prr_count,
    std::uint64_t ftr_count,
    std::uint64_t mpr_count)
{
    // Compute percent from compressed bytes if available, else record-based
    double pct = 0.0;
    // Prefer external bz2 counters if wired; fall back to local atomics
    std::uint64_t cr = compressed_read_ext_
        ? compressed_read_ext_->load(std::memory_order_relaxed)
        : compressed_read_.load(std::memory_order_relaxed);
    std::uint64_t ct = compressed_total_ext_
        ? compressed_total_ext_->load(std::memory_order_relaxed)
        : compressed_total_.load(std::memory_order_relaxed);
    if (ct > 0) {
        // Decompress + parse phase: 0% – 70%
        double fraction = static_cast<double>(cr) / static_cast<double>(ct);
        pct = calc_percent(fraction, 0.0, 70.0);
    }

    std::ostringstream os;
    os << "{\"type\":\"progress\",\"stage\":";
    write_json_string(os, stage);
    os << ",\"percent\":";
    write_json_double(os, pct);
    if (cr > 0) {
        os << ",\"compressed_bytes_read\":";
        write_json_int(os, cr);
        os << ",\"compressed_bytes_total\":";
        write_json_int(os, ct);
    }
    os << ",\"records_seen\":";
    write_json_int(os, records_seen);
    if (ptr_count > 0) {
        os << ",\"ptr_count\":";
        write_json_int(os, ptr_count);
    }
    if (prr_count > 0) {
        os << ",\"prr_count\":";
        write_json_int(os, prr_count);
    }
    if (ftr_count > 0) {
        os << ",\"ftr_count\":";
        write_json_int(os, ftr_count);
    }
    if (mpr_count > 0) {
        os << ",\"mpr_count\":";
        write_json_int(os, mpr_count);
    }
    os << '}';
    emit_json(os.str().c_str());
}

void ProgressEmitter::emit_stage(const char* stage) {
    emit_stage(stage, "");
}

void ProgressEmitter::emit_stage(const char* stage, const char* detail) {
    // Stage-based percent mapping:
    // building_tables  → 70-75%
    // writing_wide     → 75-82%
    // writing_wide_all → 82-88%
    // writing_long     → 88-92%
    // writing_summary  → 92-95%
    // validating       → 95-98%
    // finalizing       → 98-99.5%

    double pct = 70.0;
    std::string s(stage);
    if (s == "building_tables")       pct = 72.0;
    else if (s == "writing_wide")     pct = 78.0;
    else if (s == "writing_wide_all") pct = 85.0;
    else if (s == "writing_long")     pct = 90.0;
    else if (s == "writing_summary")  pct = 93.0;
    else if (s == "validating")       pct = 96.0;
    else if (s == "finalizing")       pct = 99.0;

    std::ostringstream os;
    os << "{\"type\":\"progress\",\"stage\":";
    write_json_string(os, stage);
    os << ",\"percent\":";
    write_json_double(os, pct);
    if (detail && *detail) {
        os << ",\"stage_detail\":";
        write_json_string(os, detail);
    }
    os << '}';
    emit_json(os.str().c_str());
}

void ProgressEmitter::emit_completed(std::uint64_t output_rows) {
    std::ostringstream os;
    os << "{\"type\":\"completed\",\"stage\":\"completed\",\"percent\":100.0";
    if (output_rows > 0) {
        os << ",\"output_rows\":";
        write_json_int(os, output_rows);
    }
    os << '}';
    emit_json(os.str().c_str());
}

void ProgressEmitter::emit_error(const char* stage, const char* message) {
    std::ostringstream os;
    os << "{\"type\":\"error\",\"stage\":";
    write_json_string(os, stage);
    os << ",\"message\":";
    write_json_string(os, message);
    os << '}';
    emit_json(os.str().c_str());
}

}  // namespace deepyield_stdf
