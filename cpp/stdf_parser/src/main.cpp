#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#if defined(_MSC_VER) || defined(__clang__)
#include <intrin.h>
#endif
#if __has_include(<filesystem>)
#include <filesystem>
namespace fs = std::filesystem;
#elif __has_include(<experimental/filesystem>)
#include <experimental/filesystem>
namespace fs = std::experimental::filesystem;
#else
#error "No <filesystem> or <experimental/filesystem> available"
#endif
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <type_traits>
#include <utility>
#include <vector>

#include "deepyield_stdf/bz2_streambuf.hpp"
#include "deepyield_stdf/column_registry.hpp"
#include "deepyield_stdf/logger.hpp"
#include "deepyield_stdf/parquet_writer.hpp"
#include "deepyield_stdf/perf_counters.hpp"
#include "deepyield_stdf/progress.h"
#include "deepyield_stdf/sha256.hpp"
#include "deepyield_stdf/atvi_writer.hpp"
#include "deepyield_stdf/release_import.hpp"

namespace {

using deepyield_stdf::CellValue;
using deepyield_stdf::ColumnId;
using deepyield_stdf::Row;
using deepyield_stdf::ParquetAssetWriter;

constexpr const char* kParserVersion = "deepyield-stdf-cpp-0.3.0";

// ---------- CellValue helpers (replaces old JsonValue) ----------

template <typename T>
CellValue cell_int32(std::optional<T> value) {
  if (!value) return CellValue::null();
  return CellValue::int32(static_cast<std::int32_t>(*value));
}

CellValue cell_double(std::optional<float> value) {
  if (!value) return CellValue::null();
  return CellValue::double_val(static_cast<double>(*value));
}

// long.result must be a string for mixed FTR/PTR values.
// Fast float→string matching std::ostringstream << setprecision(9) ("%.9g").
// ostringstream construction per PTR was a measurable hot-path cost.
std::string cell_result_string(std::optional<float> value) {
  if (!value) return {};
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.9g", static_cast<double>(*value));
  return std::string(buf);
}

// ---------- JSON escape (still used for timing file) ----------

std::string json_escape(const std::string& value) {
  std::ostringstream out;
  for (unsigned char ch : value) {
    switch (ch) {
      case '"':
        out << "\\\"";
        break;
      case '\\':
        out << "\\\\";
        break;
      case '\b':
        out << "\\b";
        break;
      case '\f':
        out << "\\f";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        if (ch < 0x20 || ch >= 0x80) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(ch);
        } else {
          out << ch;
        }
    }
  }
  return out.str();
}

std::string now_isoish() {
  const auto now = std::chrono::system_clock::now();
  const auto seconds = std::chrono::system_clock::to_time_t(now);
  std::tm utc{};
#if defined(_WIN32)
  gmtime_s(&utc, &seconds);
#else
  gmtime_r(&seconds, &utc);
#endif
  std::ostringstream out;
  out << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
  return out.str();
}

// Convert STDF epoch (seconds since 1970-01-01) to "YYYY-MM-DD HH:mm:ss UTC"
std::string normalize_stdf_time(std::optional<std::uint32_t> epoch) {
  if (!epoch || *epoch == 0) return "";
  const auto tt = static_cast<std::time_t>(*epoch);
  std::tm utc{};
#if defined(_WIN32)
  gmtime_s(&utc, &tt);
#else
  gmtime_r(&tt, &utc);
#endif
  std::ostringstream out;
  out << std::put_time(&utc, "%Y-%m-%d %H:%M:%S UTC");
  return out.str();
}

double elapsed_seconds(std::chrono::steady_clock::time_point start) {
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

void write_timing_file(const fs::path& path, const std::map<std::string, double>& timings) {
  std::ofstream out(path, std::ios::binary);
  if (!out) throw std::runtime_error("Unable to write parser timing file: " + path.string());
  out << "{";
  bool first = true;
  for (const auto& [key, value] : timings) {
    if (!first) out << ",";
    first = false;
    out << '"' << json_escape(key) << "\":" << std::fixed << std::setprecision(6) << value;
  }
  out << "}\n";
}

bool ends_with_ci(std::string text, std::string suffix) {
  std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  std::transform(suffix.begin(), suffix.end(), suffix.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return text.size() >= suffix.size() && text.compare(text.size() - suffix.size(), suffix.size(), suffix) == 0;
}

std::string shell_quote(const fs::path& path) {
  std::string text = path.string();
  std::string escaped;
  escaped.reserve(text.size() + 2);
  escaped.push_back('"');
  for (char ch : text) {
    if (ch == '"') escaped += "\\\"";
    else escaped.push_back(ch);
  }
  escaped.push_back('"');
  return escaped;
}

std::string shell_quote_text(const std::string& text) {
  std::string escaped;
  escaped.reserve(text.size() + 2);
  escaped.push_back('"');
  for (char ch : text) {
    if (ch == '"') escaped += "\\\"";
    else escaped.push_back(ch);
  }
  escaped.push_back('"');
  return escaped;
}

std::string python_command() {
  if (const char* configured = std::getenv("DEEPYIELD_PYTHON")) {
    if (*configured) return shell_quote_text(configured);
  }
  if (const char* configured = std::getenv("PYTHON")) {
    if (*configured) return shell_quote_text(configured);
  }
  return "python";
}

std::string system_command(const std::string& command) {
#if defined(_WIN32)
  return "cmd.exe /S /C \"" + command + "\"";
#else
  return command;
#endif
}

// ---------- STDF record parsing ----------

struct RecordHeader {
  std::uint16_t length = 0;
  std::uint8_t type = 0;
  std::uint8_t sub = 0;
};

std::uint16_t u16_from(const unsigned char* raw, bool little) {
  if (little) return static_cast<std::uint16_t>(raw[0] | (raw[1] << 8));
  return static_cast<std::uint16_t>((raw[0] << 8) | raw[1]);
}

bool read_header(std::istream& input, bool little_endian, RecordHeader& header, bool allow_far_probe) {
  unsigned char raw[4] = {0, 0, 0, 0};
  input.read(reinterpret_cast<char*>(raw), 4);
  if (input.gcount() == 0 && input.eof()) return false;
  if (input.gcount() != 4) throw std::runtime_error("Truncated STDF record header.");
  header.type = raw[2];
  header.sub = raw[3];
  if (allow_far_probe && header.type == 0 && header.sub == 10) {
    const auto little_len = u16_from(raw, true);
    const auto big_len = u16_from(raw, false);
    header.length = (little_len == 2 || big_len != 2) ? little_len : big_len;
  } else {
    header.length = u16_from(raw, little_endian);
  }
  return true;
}

// Byte view returned by PayloadReader::read().  Points into the parser's
// reusable record buffer; never owns memory.
struct ByteView {
  const std::uint8_t* ptr;
  std::size_t len;
};

class PayloadReader {
 public:
  PayloadReader(const std::uint8_t* data, std::size_t size, bool little_endian)
      : data_(data), size_(size), offset_(0), little_endian_(little_endian) {}

  std::size_t remaining() const {
    return offset_ <= size_ ? size_ - offset_ : 0;
  }

  std::optional<std::uint8_t> u1() {
    const auto raw = read(1);
    if (!raw) return std::nullopt;
    return (*raw).ptr[0];
  }

  std::optional<std::int8_t> i1() {
    const auto raw = read(1);
    if (!raw) return std::nullopt;
    return static_cast<std::int8_t>((*raw).ptr[0]);
  }

  std::optional<std::uint16_t> u2() {
    const auto raw = read(2);
    if (!raw) return std::nullopt;
    return u16_from((*raw).ptr, little_endian_);
  }

  std::optional<std::int16_t> i2() {
    const auto value = u2();
    if (!value) return std::nullopt;
    return static_cast<std::int16_t>(*value);
  }

  std::optional<std::uint32_t> u4() {
    const auto raw = read(4);
    if (!raw) return std::nullopt;
    if (little_endian_) {
      return static_cast<std::uint32_t>((*raw).ptr[0]) | (static_cast<std::uint32_t>((*raw).ptr[1]) << 8) |
             (static_cast<std::uint32_t>((*raw).ptr[2]) << 16) | (static_cast<std::uint32_t>((*raw).ptr[3]) << 24);
    }
    return (static_cast<std::uint32_t>((*raw).ptr[0]) << 24) | (static_cast<std::uint32_t>((*raw).ptr[1]) << 16) |
           (static_cast<std::uint32_t>((*raw).ptr[2]) << 8) | static_cast<std::uint32_t>((*raw).ptr[3]);
  }

  std::optional<std::int32_t> i4() {
    const auto value = u4();
    if (!value) return std::nullopt;
    return static_cast<std::int32_t>(*value);
  }

  std::optional<float> r4() {
    const auto value = u4();
    if (!value) return std::nullopt;
    float out = 0.0F;
    std::uint32_t bits = *value;
    std::memcpy(&out, &bits, sizeof(out));
    return out;
  }

  std::optional<double> r8() {
    const auto raw = read(8);
    if (!raw) return std::nullopt;
    std::uint64_t bits = 0;
    const std::uint8_t* p = (*raw).ptr;
    if (little_endian_) {
      for (int i = 7; i >= 0; --i) bits = (bits << 8) | p[static_cast<std::size_t>(i)];
    } else {
      for (int i = 0; i < 8; ++i) bits = (bits << 8) | p[static_cast<std::size_t>(i)];
    }
    double out = 0.0;
    std::memcpy(&out, &bits, sizeof(out));
    return out;
  }

  std::string c1() {
    const auto raw = read(1);
    if (!raw) return "";
    return std::string(1, static_cast<char>((*raw).ptr[0]));
  }

  std::string cn() {
    const auto length = u1();
    if (!length || *length == 0) return "";
    const auto raw = read(*length);
    if (!raw) return "";
    std::string text(reinterpret_cast<const char*>((*raw).ptr), (*raw).len);
    while (!text.empty() && text.back() == '\0') text.pop_back();
    return text;
  }

  std::vector<std::uint8_t> bn() {
    const auto length = u1();
    if (!length || *length == 0) return {};
    const auto raw = read(*length);
    if (!raw) return {};
    return std::vector<std::uint8_t>((*raw).ptr, (*raw).ptr + (*raw).len);
  }

  std::vector<std::uint8_t> dn() {
    const auto bit_count = u2();
    if (!bit_count || *bit_count == 0) return {};
    const std::size_t bytes = (*bit_count + 7) / 8;
    const auto raw = read(bytes);
    if (!raw) return {};
    return std::vector<std::uint8_t>((*raw).ptr, (*raw).ptr + (*raw).len);
  }

  void skip(std::size_t count) { (void)read(count); }

 private:
  std::optional<ByteView> read(std::size_t size) {
    if (offset_ + size > size_) {
      offset_ = size_;
      return std::nullopt;
    }
    ByteView view{data_ + offset_, size};
    offset_ += size;
    return view;
  }

  const std::uint8_t* data_;
  std::size_t size_;
  std::size_t offset_ = 0;
  bool little_endian_ = true;
};

std::string safe_column_name(std::string value) {
  std::string output;
  bool last_us = false;
  for (unsigned char ch : value) {
    const bool ok = std::isalnum(ch) || ch == '_';
    if (ok) {
      output.push_back(static_cast<char>(ch));
      last_us = false;
    } else if (!last_us) {
      output.push_back('_');
      last_us = true;
    }
  }
  while (!output.empty() && output.front() == '_') output.erase(output.begin());
  while (!output.empty() && output.back() == '_') output.pop_back();
  return output.empty() ? "UNKNOWN" : output;
}

// Phase 7A: numeric decimal-prefix check replacing std::to_string(num).rfind("700",0)
inline bool starts_with_digit_prefix(std::uint32_t num, std::uint32_t prefix) noexcept {
    if (prefix == 0) return num == 0;
    if (num < prefix) return false;
    // Scale prefix up until it's the same digit-width as num.
    std::uint64_t scale = prefix;
    while (scale <= static_cast<std::uint64_t>(num) / 10) {
        scale *= 10;
    }
    return static_cast<std::uint32_t>(num / scale) == prefix;
}

std::string test_column_name(std::uint32_t test_num, const std::string& test_text, std::optional<int> result_index = std::nullopt) {
  ++deepyield_stdf::g_perf().test_column_name_calls;
  std::string base = "TEST_" + std::to_string(test_num);
  const std::string safe = safe_column_name(test_text);
  if (safe != "UNKNOWN") base = std::to_string(test_num) + "_" + safe;
  if (result_index) base += "_" + std::to_string(*result_index);
  return base;
}

std::string pass_fail_from_part_flag(std::optional<std::uint8_t> flag) {
  if (!flag) return "";
  return ((*flag & 0b00001000) != 0) ? "FAIL" : "PASS";
}

// ---------- Parsed data structures ----------

struct TestResult {
  // Phase 3 of long-table optimization: use LongRowFixed instead of Row (map).
  // Eliminates 8 string allocations + 8 red-black tree nodes per PTR.
  deepyield_stdf::LongRowFixed long_row;
};

// Phase 4: ColumnId-based sparse row (new path, along with legacy Row).
using SparseRow = std::unordered_map<ColumnId, CellValue>;

// Phase 4: fixed column ids, allocated once at parser init.
struct FixedColumnIds {
  ColumnId col_lot_id = 0;
  ColumnId col_wafer_id = 0;
  ColumnId col_site = 0;
  ColumnId col_die_x = 0;
  ColumnId col_die_y = 0;
  ColumnId col_hbin = 0;
  ColumnId col_sbin = 0;
  ColumnId col_name = 0;
  ColumnId col_device = 0;
  ColumnId col_part_type = 0;
  ColumnId col_test_program_name = 0;
  ColumnId col_start_time = 0;
  ColumnId col_finish_time = 0;
  ColumnId col_source_file = 0;
  ColumnId col_part_id = 0;
  ColumnId col_part_index = 0;
  ColumnId col_part_flag = 0;
  ColumnId col_pass_fail = 0;
  ColumnId col_part_txt = 0;
  ColumnId col_test_time_ms = 0;
  ColumnId col_retest_index = 0;
  ColumnId col_is_retest = 0;
  ColumnId col_attempt_key = 0;
  ColumnId col_segment_id = 0;
  ColumnId col_sdr_head_num = 0;
  ColumnId col_sdr_site_num = 0;
  ColumnId col_sdr_hand_typ = 0;
  ColumnId col_sdr_hand_id = 0;
  ColumnId col_sdr_card_typ = 0;
  ColumnId col_sdr_card_id = 0;
  ColumnId col_sdr_load_typ = 0;
  ColumnId col_sdr_load_id = 0;
  ColumnId col_sdr_dib_typ = 0;
  ColumnId col_sdr_dib_id = 0;
  ColumnId col_sdr_cabl_typ = 0;
  ColumnId col_sdr_cabl_id = 0;
};

struct PartContext {
  std::optional<std::uint8_t> head_num;
  std::optional<std::uint8_t> site_num;
  int part_index = 0;
  std::string lot_id;
  std::string wafer_id;
  Row values;               // legacy string-key row
  SparseRow sparse_values;  // Phase 4: ColumnId-key row
  std::vector<TestResult> tests;
  bool use_sparse = false;  // whether to use sparse_values
};

// Phase 4: Convert SparseRow → Row for the writer boundary.
// Uses the column registry to look up names from ColumnIds.
Row sparse_row_to_row(const SparseRow& sparse, deepyield_stdf::ColumnRegistry& registry) {
  Row row;
  for (const auto& [col_id, value] : sparse) {
    const auto& meta = registry.meta(col_id);
    row[meta.name] = value;
  }
  return row;
}

// SDR per-site info (REC_TYP=1, REC_SUB=80)
struct SdrSiteInfo {
  std::uint8_t head_num = 0;
  std::uint8_t site_num = 0;
  std::string hand_typ, hand_id, card_typ, card_id;
  std::string load_typ, load_id, dib_typ, dib_id;
  std::string cabl_typ, cabl_id;
};

using SiteContextKey = std::pair<std::uint8_t, std::uint8_t>;  // (HEAD_NUM, SITE_NUM)

// Phase 3: Structured context key to avoid "head:site" string concatenation.
struct ContextKey {
  std::uint8_t head = 255;
  std::uint8_t site = 255;

  bool operator==(const ContextKey& other) const noexcept {
    return head == other.head && site == other.site;
  }
};

struct ContextKeyHash {
  std::size_t operator()(const ContextKey& k) const noexcept {
    return (static_cast<std::size_t>(k.head) << 8) ^ static_cast<std::size_t>(k.site);
  }
};

// ---------- StdfParser (refactored for direct Parquet writing) ----------

class StdfParser {
 public:
  StdfParser(fs::path source, ParquetAssetWriter& writer, bool collect_long,
             deepyield_stdf::ProgressEmitter* progress = nullptr)
      : source_(std::move(source)), writer_(writer), collect_long_(collect_long),
        progress_(progress) {
    init_fixed_columns();
  }

  void set_use_sparse_row(bool use) { use_sparse_row_ = use; }
  void set_use_catalog_vector(bool use) { use_catalog_vector_ = use; }
  void set_test_filter(std::unordered_set<std::uint32_t> test_nums,
                       std::unordered_set<std::string> test_names) {
    included_test_nums_ = std::move(test_nums);
    included_test_names_.clear();
    for (const auto& name : test_names) {
      std::string normalized = name;
      std::transform(normalized.begin(), normalized.end(), normalized.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
      });
      included_test_names_.insert(std::move(normalized));
    }
  }

  // Direct ATVI generation (plan WP3): public entry points used by main().
  void enable_atvi(const fs::path& dir, const std::string& project_type,
                   const std::string& source_file_hash) {
    atvi_enabled_ = true;
    atvi_project_type_ = project_type.empty() ? "UNKNOWN" : project_type;
    source_file_hash_ = source_file_hash;
    atvi_writer_ = std::make_unique<deepyield_stdf::AtviWriter>(dir, 8);
    atvi_writer_->set_source_file_hash(source_file_hash);
  }

  void finish_atvi() {
    if (!atvi_writer_) return;
    std::cerr << "[stdf2parquet] ATVI keys="
              << std::to_string(atvi_writer_->observation_key_count())
              << " rows=" << std::to_string(atvi_writer_->total_row_count()) << "\n";
    const std::size_t keys = atvi_writer_->observation_key_count();
    std::uint32_t buckets = keys <= 128 ? 8 : keys <= 512 ? 32 : keys <= 2048 ? 64 : 128;
    atvi_writer_->set_bucket_count(buckets);
    atvi_writer_->finish();
  }

  std::int64_t atvi_emit_ticks() const { return atvi_emit_ticks_; }

  void init_fixed_columns() {
    // Phase 4: Register all fixed (identity + meta) columns once.
    auto& reg = column_registry_;
    fc_.col_lot_id       = reg.get_or_create_fixed_column("LOT_ID", CellValue::String);
    fc_.col_wafer_id     = reg.get_or_create_fixed_column("WAFER_ID", CellValue::String);
    fc_.col_site         = reg.get_or_create_fixed_column("SITE", CellValue::Int32);
    fc_.col_die_x        = reg.get_or_create_fixed_column("DIE_X", CellValue::Int32);
    fc_.col_die_y        = reg.get_or_create_fixed_column("DIE_Y", CellValue::Int32);
    fc_.col_hbin         = reg.get_or_create_fixed_column("HBIN", CellValue::Int32);
    fc_.col_sbin         = reg.get_or_create_fixed_column("SBIN", CellValue::Int32);
    fc_.col_name         = reg.get_or_create_fixed_column("Name", CellValue::String);
    fc_.col_device       = reg.get_or_create_fixed_column("device", CellValue::String);
    fc_.col_part_type    = reg.get_or_create_fixed_column("part_type", CellValue::String);
    fc_.col_test_program_name = reg.get_or_create_fixed_column("test_program_name", CellValue::String);
    fc_.col_start_time   = reg.get_or_create_fixed_column("start_time", CellValue::String);
    fc_.col_finish_time  = reg.get_or_create_fixed_column("finish_time", CellValue::String);
    fc_.col_source_file  = reg.get_or_create_fixed_column("__source_file", CellValue::String);
    fc_.col_part_id      = reg.get_or_create_fixed_column("__part_id", CellValue::String);
    fc_.col_part_index   = reg.get_or_create_fixed_column("__part_index", CellValue::Int32);
    fc_.col_part_flag    = reg.get_or_create_fixed_column("__part_flag", CellValue::Int32);
    fc_.col_pass_fail    = reg.get_or_create_fixed_column("__pass_fail", CellValue::String);
    fc_.col_part_txt     = reg.get_or_create_fixed_column("__part_txt", CellValue::String);
    fc_.col_test_time_ms = reg.get_or_create_fixed_column("__test_time_ms", CellValue::Int32);
    fc_.col_retest_index  = reg.get_or_create_fixed_column("__retest_index", CellValue::Int32);
    fc_.col_is_retest    = reg.get_or_create_fixed_column("__is_retest", CellValue::Bool);
    fc_.col_attempt_key  = reg.get_or_create_fixed_column("__attempt_key", CellValue::String);
    fc_.col_segment_id   = reg.get_or_create_fixed_column("__segment_id", CellValue::String);
    // SDR columns
    fc_.col_sdr_head_num  = reg.get_or_create_fixed_column("SDR_HEAD_NUM", CellValue::Int32);
    fc_.col_sdr_site_num  = reg.get_or_create_fixed_column("SDR_SITE_NUM", CellValue::Int32);
    fc_.col_sdr_hand_typ  = reg.get_or_create_fixed_column("SDR_HAND_TYP", CellValue::String);
    fc_.col_sdr_hand_id   = reg.get_or_create_fixed_column("SDR_HAND_ID", CellValue::String);
    fc_.col_sdr_card_typ  = reg.get_or_create_fixed_column("SDR_CARD_TYP", CellValue::String);
    fc_.col_sdr_card_id   = reg.get_or_create_fixed_column("SDR_CARD_ID", CellValue::String);
    fc_.col_sdr_load_typ  = reg.get_or_create_fixed_column("SDR_LOAD_TYP", CellValue::String);
    fc_.col_sdr_load_id   = reg.get_or_create_fixed_column("SDR_LOAD_ID", CellValue::String);
    fc_.col_sdr_dib_typ   = reg.get_or_create_fixed_column("SDR_DIB_TYP", CellValue::String);
    fc_.col_sdr_dib_id    = reg.get_or_create_fixed_column("SDR_DIB_ID", CellValue::String);
    fc_.col_sdr_cabl_typ  = reg.get_or_create_fixed_column("SDR_CABL_TYP", CellValue::String);
    fc_.col_sdr_cabl_id   = reg.get_or_create_fixed_column("SDR_CABL_ID", CellValue::String);
    // Also register legacy "Lot_ID"
    reg.get_or_create_fixed_column("Lot_ID", CellValue::String);
  }

  void set_progress(deepyield_stdf::ProgressEmitter* progress) {
    progress_ = progress;
  }

  // Cheap timestamp for parse-loop timing.  Uses the TSC counter (~5 ns)
  // instead of steady_clock (~190 ns on clang/Windows) so the instrumentation
  // itself does not distort the per-record hot path.  Emitted values are raw
  // CPU ticks; only their ratios are meaningful.
  static std::int64_t now_ticks() {
#if defined(_MSC_VER) || defined(__clang__)
    return static_cast<std::int64_t>(__rdtsc());
#else
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
#endif
  }

  static bool check_parse_timing_env() {
    const char* env = std::getenv("DEEPYIELD_PARSE_TIMING");
    return env && (std::string(env) == "1" || std::string(env) == "true");
  }

  void emit_parse_timing(std::ostream& out) const {
    if (!timing_enabled_) return;
    out << "{\"parse_timing_ticks\": {"
        << "\"io_ticks\":" << timing_io_ns_
        << ",\"parse_record_ticks\":" << timing_parse_record_ns_
        << ",\"ptr_ticks\":" << timing_ptr_ns_
        << ",\"ptr_registry_ticks\":" << timing_ptr_registry_ns_
        << ",\"ptr_sparse_ticks\":" << timing_ptr_sparse_ns_
        << ",\"ptr_catalog_ticks\":" << timing_ptr_catalog_ns_
        << ",\"prr_ticks\":" << timing_prr_ns_
        << ",\"prr_materialize_ticks\":" << timing_prr_materialize_ns_
        << ",\"other_records_ticks\":" << timing_other_records_ns_
        << "}}\n";
    out.flush();
  }

  void parse(std::istream& input) {
    RecordHeader header;
    bool allow_far_probe = true;
    std::uint64_t total_records = 0;
    auto last_progress = std::chrono::steady_clock::now();
    const auto progress_interval = std::chrono::seconds(2);
    timing_enabled_ = check_parse_timing_env();

    while (read_header(input, little_endian_, header, allow_far_probe)) {
      allow_far_probe = false;
      const std::int64_t io_t0 = timing_enabled_ ? now_ticks() : 0;
      record_buf_.resize(header.length);
      input.read(reinterpret_cast<char*>(record_buf_.data()), static_cast<std::streamsize>(header.length));
      if (input.gcount() != static_cast<std::streamsize>(header.length)) {
        throw std::runtime_error("Truncated STDF record payload.");
      }
      if (timing_enabled_) timing_io_ns_ += now_ticks() - io_t0;
      const std::int64_t pr_t0 = timing_enabled_ ? now_ticks() : 0;
      parse_record(header, record_buf_.data(), static_cast<std::size_t>(header.length));
      if (timing_enabled_) timing_parse_record_ns_ += now_ticks() - pr_t0;
      ++total_records;
      ++deepyield_stdf::g_perf().total_records;

      // Emit progress every ~2 seconds during parsing (Step 9)
      if (progress_) {
        auto now = std::chrono::steady_clock::now();
        if (now - last_progress >= progress_interval) {
          std::uint64_t prr = record_count("PRR");
          progress_->emit_parse_progress(
              "parsing_stdf", total_records,
              record_count("PTR"), prr,
              record_count("FTR"), record_count("MPR"));
          last_progress = now;
        }
      }
    }
  }

  void parse(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("Unable to open input: " + path.string());
    parse(input);
  }

  std::uint64_t record_count(const std::string& name) const {
    auto it = record_counts_.find(name);
    return (it != record_counts_.end()) ? static_cast<std::uint64_t>(it->second) : 0;
  }

  // Give the writer the ColumnId → name map once all test columns have been
  // discovered (must run after parse, before finalize_and_close).  Required by
  // the ColumnId-keyed wide write path.
  void propagate_column_info() {
    std::vector<std::string> id_to_name;
    const auto& cols = column_registry_.columns_in_order();
    id_to_name.reserve(cols.size());
    for (const auto& meta : cols) id_to_name.push_back(meta.name);
    writer_.set_column_info(std::move(id_to_name), fc_.col_attempt_key);
  }

  std::vector<std::string> finalize_and_close(const fs::path& original_source,
                                               const std::string& file_hash) {
    // Emit progress: building tables
    if (progress_) progress_->emit_stage("building_tables", "closing row groups");

    if (use_catalog_vector_) {
      for (std::size_t idx = 0; idx < catalog_vec_.size(); ++idx) {
        if (catalog_seen_[idx]) {
          writer_.append_catalog(catalog_vec_[idx]);
        }
      }
    } else {
      for (const auto& [key, row] : catalog_) {
        (void)key;
        writer_.append_catalog(row);
      }
    }

    if (progress_) progress_->emit_stage("building_tables", "catalog appended");

    std::map<std::string, std::string> stdf_metadata;
    stdf_metadata["lot_id"] = current_lot_id_;
    stdf_metadata["wafer_id"] = current_wafer_id_;
    stdf_metadata["part_type"] = part_type_;
    stdf_metadata["program_name"] = program_name_;
    stdf_metadata["program_revision"] = program_revision_;
    if (mir_setup_time_) stdf_metadata["mir_setup_time"] = std::to_string(*mir_setup_time_);
    if (mir_start_time_) stdf_metadata["mir_start_time"] = std::to_string(*mir_start_time_);
    if (wrr_finish_time_) stdf_metadata["wrr_finish_time"] = std::to_string(*wrr_finish_time_);
    if (mrr_finish_time_) stdf_metadata["mrr_finish_time"] = std::to_string(*mrr_finish_time_);
    stdf_metadata["sdr_record_count"] = std::to_string(sdr_record_count_);
    stdf_metadata["sdr_site_count"] = std::to_string(static_cast<int>(site_context_.size()));

    auto wide_columns = writer_.close();

    std::string source_type = ends_with_ci(original_source.string(), ".bz2") ? "stdf_bz2" : "stdf";
    std::uint64_t file_size = 0;
    std::error_code ec;
    file_size = fs::file_size(original_source, ec);
    if (ec) file_size = 0;

    writer_.write_manifest(original_source.string(), source_type, file_size,
                            file_hash, record_counts_, stdf_metadata, warnings_, wide_columns);

    return wide_columns;
  }

 private:
  std::string record_name(std::uint8_t type, std::uint8_t sub) const {
    static const std::map<std::pair<int, int>, std::string> names = {
        {{0, 10}, "FAR"}, {{1, 10}, "MIR"}, {{1, 20}, "MRR"}, {{1, 80}, "SDR"},
        {{2, 10}, "WIR"}, {{2, 20}, "WRR"},
        {{5, 10}, "PIR"}, {{5, 20}, "PRR"}, {{15, 10}, "PTR"}, {{15, 15}, "MPR"}, {{15, 20}, "FTR"}};
    const auto found = names.find({type, sub});
    if (found != names.end()) return found->second;
    return std::to_string(type) + ":" + std::to_string(sub);
  }

  void parse_record(const RecordHeader& header, const std::uint8_t* data, std::size_t size) {
    const auto name = record_name(header.type, header.sub);
    record_counts_[name] += 1;
    PayloadReader reader(data, size, little_endian_);
    const std::int64_t t0 = timing_enabled_ ? now_ticks() : 0;
    try {
      if (name == "FAR") parse_far(reader);
      else if (name == "MIR") parse_mir(reader);
      else if (name == "MRR") parse_mrr(reader);
      else if (name == "WIR") parse_wir(reader);
      else if (name == "WRR") parse_wrr(reader);
      else if (name == "PIR") parse_pir(reader);
      else if (name == "PTR") parse_ptr(reader);
      else if (name == "MPR") parse_mpr(reader);
      else if (name == "FTR") parse_ftr(reader);
      else if (name == "PRR") parse_prr(reader);
      else if (name == "SDR") parse_sdr(reader);
      else warnings_.push_back("Skipped unsupported STDF record " + name + ".");
    } catch (const std::exception& exc) {
      warnings_.push_back("Failed to parse " + name + " record: " + exc.what());
    }
    if (timing_enabled_) {
      if (name == "PTR") timing_ptr_ns_ += now_ticks() - t0;
      else if (name == "PRR") timing_prr_ns_ += now_ticks() - t0;
      else timing_other_records_ns_ += now_ticks() - t0;
    }
  }

  void parse_far(PayloadReader& reader) {
    const auto cpu_type = reader.u1();
    (void)reader.u1();
    if (cpu_type == 1) little_endian_ = false;
    if (cpu_type == 2) little_endian_ = true;
  }

  void parse_mir(PayloadReader& reader) {
    if (segment_started_) ++segment_ordinal_;
    segment_started_ = true;
    mir_setup_time_ = reader.u4();
    mir_start_time_ = reader.u4();
    reader.u1();                   // STAT_NUM
    reader.c1();                   // MODE_COD
    reader.c1();                   // RTST_COD
    reader.c1();                   // PROT_COD
    reader.u2();                   // BURN_TIM
    reader.c1();                   // CMOD_COD
    current_lot_id_ = reader.cn(); // LOT_ID
    part_type_ = reader.cn();      // PART_TYP → device
    reader.cn();                   // NODE_NAM
    reader.cn();                   // TSTR_NAM
    program_name_ = reader.cn();   // JOB_NAM
    program_revision_ = reader.cn();
    while (reader.remaining()) reader.cn();
  }

  void parse_mrr(PayloadReader& reader) {
    mrr_finish_time_ = reader.u4();
    reader.c1();
    mrr_user_text_ = reader.cn();
    reader.cn();
  }

  // SDR: Site Description Record (REC_TYP=1, REC_SUB=80)
  void parse_sdr(PayloadReader& reader) {
    const auto head_num_opt = reader.u1();
    (void)reader.u1();  // SITE_GRP
    const auto site_count_opt = reader.u1();
    if (!head_num_opt || !site_count_opt) return;
    const auto head_num = *head_num_opt;
    const auto site_count = *site_count_opt;
    sdr_record_count_++;

    for (std::uint8_t sn = 0; sn < site_count && reader.remaining() >= 11; ++sn) {
      const auto site_num_opt = reader.u1();
      if (!site_num_opt) break;
      SdrSiteInfo info;
      info.head_num = head_num;
      info.site_num = *site_num_opt;
      info.hand_typ = reader.c1();  info.hand_id = reader.c1();
      info.card_typ = reader.c1();  info.card_id = reader.c1();
      info.load_typ = reader.c1();  info.load_id = reader.c1();
      info.dib_typ  = reader.c1();  info.dib_id  = reader.c1();
      info.cabl_typ = reader.c1();  info.cabl_id = reader.c1();
      site_context_[std::make_pair(head_num, info.site_num)] = std::move(info);
    }
    // Consume any remaining bytes (extended fields we don't parse)
    while (reader.remaining()) reader.u1();
  }

  // Inject SDR fields into context values with "SDR_" prefix
  void inject_sdr_into_context(Row& values, std::optional<std::uint8_t> head, std::optional<std::uint8_t> site) {
    if (!head || !site) return;
    auto it = site_context_.find({*head, *site});
    if (it != site_context_.end()) {
      const auto& info = it->second;
      values["SDR_HEAD_NUM"] = CellValue::int32(info.head_num);
      values["SDR_SITE_NUM"] = CellValue::int32(info.site_num);
      values["SDR_HAND_TYP"] = CellValue::string(info.hand_typ);
      values["SDR_HAND_ID"]  = CellValue::string(info.hand_id);
      values["SDR_CARD_TYP"] = CellValue::string(info.card_typ);
      values["SDR_CARD_ID"]  = CellValue::string(info.card_id);
      values["SDR_LOAD_TYP"] = CellValue::string(info.load_typ);
      values["SDR_LOAD_ID"]  = CellValue::string(info.load_id);
      values["SDR_DIB_TYP"]  = CellValue::string(info.dib_typ);
      values["SDR_DIB_ID"]   = CellValue::string(info.dib_id);
      values["SDR_CABL_TYP"] = CellValue::string(info.cabl_typ);
      values["SDR_CABL_ID"]  = CellValue::string(info.cabl_id);
    } else {
      values["SDR_HEAD_NUM"] = CellValue::null();
      values["SDR_SITE_NUM"] = CellValue::null();
      values["SDR_HAND_TYP"] = CellValue::string("UNKNOWN");
      values["SDR_HAND_ID"]  = CellValue::string("UNKNOWN");
      values["SDR_CARD_TYP"] = CellValue::string("UNKNOWN");
      values["SDR_CARD_ID"]  = CellValue::string("UNKNOWN");
      values["SDR_LOAD_TYP"] = CellValue::string("UNKNOWN");
      values["SDR_LOAD_ID"]  = CellValue::string("UNKNOWN");
      values["SDR_DIB_TYP"]  = CellValue::string("UNKNOWN");
      values["SDR_DIB_ID"]   = CellValue::string("UNKNOWN");
      values["SDR_CABL_TYP"] = CellValue::string("UNKNOWN");
      values["SDR_CABL_ID"]  = CellValue::string("UNKNOWN");
    }
  }

  void parse_wir(PayloadReader& reader) {
    reader.u1();
    reader.u1();
    reader.u4();
    const auto wafer = reader.cn();
    if (!wafer.empty()) current_wafer_id_ = wafer;
  }

  void parse_wrr(PayloadReader& reader) {
    reader.u1();
    reader.u1();
    wrr_finish_time_ = reader.u4();
    reader.u4();
    reader.u4();
    reader.u4();
    reader.u4();
    reader.u4();
    const auto wafer = reader.cn();
    if (!wafer.empty()) current_wafer_id_ = wafer;
  }

  ContextKey context_key(std::optional<std::uint8_t> head, std::optional<std::uint8_t> site) const {
    // Phase 3: No more string concatenation — pure struct construction.
    return ContextKey{head.value_or(255), site.value_or(255)};
  }

  std::string current_segment_id() const {
    std::ostringstream out;
    out << "segment-" << std::setw(4) << std::setfill('0') << segment_ordinal_;
    return out.str();
  }

  PartContext& get_context(std::optional<std::uint8_t> head, std::optional<std::uint8_t> site) {
    const auto key = context_key(head, site);
    auto found = contexts_.find(key);
    if (found == contexts_.end()) {
      PartContext context;
      context.head_num = head;
      context.site_num = site;
      context.part_index = ++part_counter_;
      context.lot_id = current_lot_id_;
      context.wafer_id = current_wafer_id_;
      context.use_sparse = use_sparse_row_;
      // Avoid repeated reallocation as the per-device long-rows vector grows.
      context.tests.reserve(static_cast<std::size_t>(column_registry_.size() + 64));
      // Phase 7C: when sparse is default, skip legacy values entirely
      if (!use_sparse_row_) {
        context.values["Lot_ID"] = CellValue::string(current_lot_id_);
        context.values["LOT_ID"] = CellValue::string(current_lot_id_);
        context.values["WAFER_ID"] = CellValue::string(current_wafer_id_);
        context.values["SITE"] = cell_int32(site);
        context.values["device"] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
        context.values["part_type"] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
        context.values["test_program_name"] = CellValue::string(program_name_);
        context.values["start_time"] = CellValue::string(normalize_stdf_time(mir_start_time_));
        context.values["finish_time"] = CellValue::string(normalize_stdf_time(mrr_finish_time_));
        context.values["__source_file"] = CellValue::string(source_.string());
        context.values["__segment_id"] = CellValue::string(current_segment_id());
        inject_sdr_into_context(context.values, head, site);
      }
      // Phase 4/7C: Populate sparse_values with ColumnId keys (default path)
      if (use_sparse_row_) {
        auto& sv = context.sparse_values;
        sv.reserve(64);
        sv[fc_.col_lot_id] = CellValue::string(current_lot_id_);
        sv[fc_.col_wafer_id] = CellValue::string(current_wafer_id_);
        sv[fc_.col_site] = cell_int32(site);
        sv[fc_.col_device] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
        sv[fc_.col_part_type] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
        sv[fc_.col_test_program_name] = CellValue::string(program_name_);
        sv[fc_.col_start_time] = CellValue::string(normalize_stdf_time(mir_start_time_));
        sv[fc_.col_finish_time] = CellValue::string(normalize_stdf_time(mrr_finish_time_));
        sv[fc_.col_source_file] = CellValue::string(source_.string());
        sv[fc_.col_segment_id] = CellValue::string(current_segment_id());
        inject_sdr_into_sparse(sv, head, site);
      }
      found = contexts_.emplace(key, std::move(context)).first;
    }
    return found->second;
  }

  // Phase 4: SDR injection into sparse row
  void inject_sdr_into_sparse(SparseRow& sv, std::optional<std::uint8_t> head, std::optional<std::uint8_t> site) {
    if (!head || !site) return;
    auto it = site_context_.find({*head, *site});
    if (it != site_context_.end()) {
      const auto& info = it->second;
      sv[fc_.col_sdr_head_num] = CellValue::int32(info.head_num);
      sv[fc_.col_sdr_site_num] = CellValue::int32(info.site_num);
      sv[fc_.col_sdr_hand_typ] = CellValue::string(info.hand_typ);
      sv[fc_.col_sdr_hand_id]  = CellValue::string(info.hand_id);
      sv[fc_.col_sdr_card_typ] = CellValue::string(info.card_typ);
      sv[fc_.col_sdr_card_id]  = CellValue::string(info.card_id);
      sv[fc_.col_sdr_load_typ] = CellValue::string(info.load_typ);
      sv[fc_.col_sdr_load_id]  = CellValue::string(info.load_id);
      sv[fc_.col_sdr_dib_typ]  = CellValue::string(info.dib_typ);
      sv[fc_.col_sdr_dib_id]   = CellValue::string(info.dib_id);
      sv[fc_.col_sdr_cabl_typ] = CellValue::string(info.cabl_typ);
      sv[fc_.col_sdr_cabl_id]  = CellValue::string(info.cabl_id);
    }
  }

  void parse_pir(PayloadReader& reader) {
    const auto head = reader.u1();
    const auto site = reader.u1();
    PartContext context;
    context.head_num = head;
    context.site_num = site;
    context.part_index = ++part_counter_;
    context.lot_id = current_lot_id_;
    context.wafer_id = current_wafer_id_;
    context.use_sparse = use_sparse_row_;
    // Phase 7C: sparse default — write only to sparse_values
    if (use_sparse_row_) {
      context.sparse_values[fc_.col_lot_id] = CellValue::string(current_lot_id_);
      context.sparse_values[fc_.col_wafer_id] = CellValue::string(current_wafer_id_);
      context.sparse_values[fc_.col_site] = cell_int32(site);
      context.sparse_values[fc_.col_device] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
      context.sparse_values[fc_.col_part_type] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
      context.sparse_values[fc_.col_test_program_name] = CellValue::string(program_name_);
      context.sparse_values[fc_.col_start_time] = CellValue::string(normalize_stdf_time(mir_start_time_));
      context.sparse_values[fc_.col_finish_time] = CellValue::string(normalize_stdf_time(mrr_finish_time_));
      context.sparse_values[fc_.col_part_index] = CellValue::int32(context.part_index);
        context.sparse_values[fc_.col_source_file] = CellValue::string(source_.string());
        context.sparse_values[fc_.col_segment_id] = CellValue::string(current_segment_id());
      inject_sdr_into_sparse(context.sparse_values, head, site);
    } else {
      context.values["Lot_ID"] = CellValue::string(current_lot_id_);
      context.values["LOT_ID"] = CellValue::string(current_lot_id_);
      context.values["WAFER_ID"] = CellValue::string(current_wafer_id_);
      context.values["SITE"] = cell_int32(site);
      context.values["device"] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
      context.values["part_type"] = CellValue::string(part_type_.empty() ? "UNKNOWN_DEVICE" : part_type_);
      context.values["test_program_name"] = CellValue::string(program_name_);
      context.values["start_time"] = CellValue::string(normalize_stdf_time(mir_start_time_));
      context.values["finish_time"] = CellValue::string(normalize_stdf_time(mrr_finish_time_));
      context.values["__part_index"] = CellValue::int32(context.part_index);
        context.values["__source_file"] = CellValue::string(source_.string());
        context.values["__segment_id"] = CellValue::string(current_segment_id());
      inject_sdr_into_context(context.values, head, site);
    }
    contexts_[context_key(head, site)] = std::move(context);
  }

  // Compact long row (plan §6.1 — Task 7).
  // part_index + attempt_index are filled in later by parse_prr().
  // Phase 3 of long-table optimization: returns LongRowFixed (POD), not Row (map).
  deepyield_stdf::LongRowFixed base_long_row(std::uint32_t test_num,
                    std::string result, bool is_fail,
                    std::int32_t test_flag, std::int32_t param_flag,
                    const std::string& record_type,
                    std::int32_t result_index = -1,
                    std::uint32_t column_id = 0) {
    deepyield_stdf::LongRowFixed row;
    // part_index = -1 (null sentinel), attempt_index = 0 (default)
    row.test_num = static_cast<std::int32_t>(test_num);
    // result: PTR → formatted string, FTR → text; both are strings in long.parquet
    row.result = std::move(result);
    row.is_fail = is_fail;
    row.test_flag = test_flag;
    row.param_flag = param_flag;
    row.record_sequence = ++long_record_sequence_;
    row.record_type = record_type;
    row.result_index = result_index;
    const std::string result_index_text = result_index >= 0 ? std::to_string(result_index) : "-";
    row.test_item_key = deepyield_stdf::test_item_key_raw(
        deepyield_stdf::test_item_payload(record_type, test_num, result_index_text));
    row.column_id = column_id;
    return row;
  }

  // Compute is_fail (plan §6.3 — Task 7).
  // Priority 1: STDF test_flag bit 0 (alarm / fail).
  // Priority 2: lsl/usl comparison.
  // Priority 3: false.
  static bool compute_is_fail(std::optional<std::uint8_t> test_flag,
                              std::optional<float> result,
                              double low_val, double high_val,
                              bool has_limits) {
    // Priority 1: STDF flag bit 0
    if (test_flag && (*test_flag & 0x01)) return true;

    // Priority 2: numeric limits comparison
    if (has_limits && result) {
      double r = static_cast<double>(*result);
      if (r < low_val || r > high_val) return true;
    }

    return false;
  }

  void update_catalog(std::uint32_t test_num, const std::string& column, const std::string& record_type, const std::string& test_name,
                      CellValue low_limit, CellValue high_limit, const std::string& units) {
    Row& row = catalog_[column];
    row["test_id"] = CellValue::string(column);
    row["test_num"] = CellValue::int32(static_cast<std::int32_t>(test_num));
    row["record_type"] = CellValue::string(record_type);
    row["name"] = CellValue::string(test_name.empty() ? column : test_name);
    row["unit"] = CellValue::string(units);
    if (row.find("low_limit") == row.end() || row["low_limit"].is_null()) row["low_limit"] = low_limit;
    if (row.find("high_limit") == row.end() || row["high_limit"].is_null()) row["high_limit"] = high_limit;
  }

  // Phase 7B: catalog vector indexed by ColumnId — O(1) by direct array index.
  // Hot path: catalog_seen_[idx]==true → immediate return, no registry lookup.
  void update_catalog_by_id(ColumnId column_id, std::uint32_t test_num,
                             const std::string& record_type, const std::string& test_name,
                             CellValue low_limit, CellValue high_limit, const std::string& units) {
    const std::size_t idx = static_cast<std::size_t>(column_id);
    if (idx >= catalog_vec_.size()) {
      catalog_vec_.resize(idx + 1);
      catalog_seen_.resize(idx + 1, 0);
    }

    Row& row = catalog_vec_[idx];
    if (!catalog_seen_[idx]) {
      // First occurrence: populate identity fields from registry.
      catalog_seen_[idx] = 1;
      const auto& column = column_registry_.meta(column_id).name;
      row["test_id"] = CellValue::string(column);
      row["test_num"] = CellValue::int32(static_cast<std::int32_t>(test_num));
      row["record_type"] = CellValue::string(record_type);
      row["name"] = CellValue::string(test_name.empty() ? column : test_name);
    }
    // These fields may vary across PTRs: always overwrite (matches legacy semantics).
    row["unit"] = CellValue::string(units);
    if (low_limit.type != CellValue::Null) row["low_limit"] = low_limit;
    if (high_limit.type != CellValue::Null) row["high_limit"] = high_limit;
  }

  void parse_ptr(PayloadReader& reader) {
    ++deepyield_stdf::g_perf().ptr_count;
    const auto test_num = reader.u4();
    const auto head = reader.u1();
    const auto site = reader.u1();
    const auto test_flag = reader.u1();
    const auto param_flag = reader.u1();
    const auto result = reader.r4();
    if (!test_num) return;
    if (has_test_filter() && included_test_names_.empty() &&
        included_test_nums_.count(*test_num) == 0) {
      return;
    }
    const auto test_text = reader.cn();
    if (!should_include_test(*test_num, test_text)) return;
    reader.cn();
    if (reader.remaining()) reader.u1();
    if (reader.remaining()) reader.i1();
    if (reader.remaining()) reader.i1();
    if (reader.remaining()) reader.i1();
    CellValue low = CellValue::null();
    CellValue high = CellValue::null();
    if (reader.remaining() >= 4) low = cell_double(reader.r4());
    if (reader.remaining() >= 4) high = cell_double(reader.r4());
    const auto units = reader.remaining() ? reader.cn() : "";
    auto& context = get_context(head, site);
    const std::int64_t reg_t0 = timing_enabled_ ? now_ticks() : 0;
    ColumnId column_id = 0;
    const std::uint32_t tn = *test_num;
    if (tn >= test_col_by_num_.size()) {
      test_col_by_num_.resize(static_cast<std::size_t>(tn) + 1, static_cast<ColumnId>(-1));
    }
    column_id = test_col_by_num_[tn];
    if (column_id == static_cast<ColumnId>(-1)) {
      column_id = column_registry_.get_or_create_test_column(tn, test_text, 0, CellValue::Double);
      test_col_by_num_[tn] = column_id;
    }
    if (timing_enabled_) timing_ptr_registry_ns_ += now_ticks() - reg_t0;
    CellValue cell_val = cell_double(result);
    // Phase 7C: sparse is default — write ONLY to sparse_values, NOT values
    const std::int64_t sp_t0 = timing_enabled_ ? now_ticks() : 0;
    if (context.use_sparse) {
      context.sparse_values[column_id] = std::move(cell_val);
    } else {
      const auto& col_name = column_registry_.meta(column_id).name;
      context.values[col_name] = std::move(cell_val);
    }
    if (timing_enabled_) timing_ptr_sparse_ns_ += now_ticks() - sp_t0;
    if (starts_with_digit_prefix(*test_num, 700)) {
      context.values[std::to_string(*test_num)] = cell_double(result);
    }
    if (collect_long_) {
      bool fail_flag = compute_is_fail(test_flag, result,
          low.is_null() ? 0.0 : low.dbl_val,
          high.is_null() ? 0.0 : high.dbl_val,
          !low.is_null() && !high.is_null());
      context.tests.push_back({base_long_row(*test_num, cell_result_string(result), fail_flag,
          test_flag ? static_cast<std::int32_t>(*test_flag) : 0,
          param_flag ? static_cast<std::int32_t>(*param_flag) : 0, "PTR", -1, column_id)});
    }
    const std::int64_t cat_t0 = timing_enabled_ ? now_ticks() : 0;
    if (use_catalog_vector_)
      update_catalog_by_id(column_id, *test_num, "PTR", test_text, low, high, units);
    else {
      const auto& col_name = column_registry_.meta(column_id).name;
      update_catalog(*test_num, col_name, "PTR", test_text, low, high, units);
    }
    if (timing_enabled_) timing_ptr_catalog_ns_ += now_ticks() - cat_t0;
  }

  void parse_mpr(PayloadReader& reader) {
    ++deepyield_stdf::g_perf().mpr_count;
    const auto test_num = reader.u4();
    const auto head = reader.u1();
    const auto site = reader.u1();
    const auto test_flag = reader.u1();
    const auto param_flag = reader.u1();
    if (!test_num) return;
    if (has_test_filter() && included_test_names_.empty() &&
        included_test_nums_.count(*test_num) == 0) {
      return;
    }
    const int rtn_count = static_cast<int>(reader.u2().value_or(0));
    const int result_count = static_cast<int>(reader.u2().value_or(0));
    for (int i = 0; i < rtn_count && reader.remaining(); ++i) reader.u1();
    std::vector<std::optional<float>> results;
    for (int i = 0; i < result_count && reader.remaining() >= 4; ++i) results.push_back(reader.r4());
    const auto test_text = reader.remaining() ? reader.cn() : "";
    if (!should_include_test(*test_num, test_text)) return;
    if (reader.remaining()) reader.cn();
    if (reader.remaining()) reader.u1();
    if (reader.remaining()) reader.i1();
    if (reader.remaining()) reader.i1();
    if (reader.remaining()) reader.i1();
    CellValue low = CellValue::null();
    CellValue high = CellValue::null();
    if (reader.remaining() >= 4) low = cell_double(reader.r4());
    if (reader.remaining() >= 4) high = cell_double(reader.r4());
    const auto units = reader.remaining() ? reader.cn() : "";
    bool has_limits = !low.is_null() && !high.is_null();
    double lo = has_limits ? low.dbl_val : 0.0;
    double hi = has_limits ? high.dbl_val : 0.0;
    auto& context = get_context(head, site);
    for (std::size_t index = 0; index < results.size(); ++index) {
      ColumnId column_id = column_registry_.get_or_create_test_column(*test_num, test_text, static_cast<std::uint32_t>(index), CellValue::Double);
      CellValue cell_val = cell_double(results[index]);
      // Phase 7C: sparse is default — write ONLY to sparse_values
      if (context.use_sparse) {
        context.sparse_values[column_id] = std::move(cell_val);
      } else {
        const auto& col_name = column_registry_.meta(column_id).name;
        context.values[col_name] = std::move(cell_val);
      }
      if (collect_long_) {
        bool fail_flag = compute_is_fail(test_flag, results[index], lo, hi, has_limits);
        context.tests.push_back({base_long_row(*test_num, cell_result_string(results[index]), fail_flag,
            test_flag ? static_cast<std::int32_t>(*test_flag) : 0,
            param_flag ? static_cast<std::int32_t>(*param_flag) : 0, "MPR",
            static_cast<std::int32_t>(index), column_id)});
      }
      if (use_catalog_vector_)
        update_catalog_by_id(column_id, *test_num, "MPR", test_text, low, high, units);
      else {
        const auto& col_name = column_registry_.meta(column_id).name;
        update_catalog(*test_num, col_name, "MPR", test_text, low, high, units);
      }
    }
  }

  void parse_ftr(PayloadReader& reader) {
    ++deepyield_stdf::g_perf().ftr_count;
    const auto test_num = reader.u4();
    const auto head = reader.u1();
    const auto site = reader.u1();
    const auto test_flag = reader.u1();
    const auto opt_flag = reader.u1();
    if (!test_num) return;
    if (has_test_filter() && included_test_names_.empty() &&
        included_test_nums_.count(*test_num) == 0) {
      return;
    }
    if (reader.remaining() >= 28) reader.skip(28);
    const int rtn_count = reader.remaining() >= 2 ? static_cast<int>(reader.u2().value_or(0)) : 0;
    const int pgm_count = reader.remaining() >= 2 ? static_cast<int>(reader.u2().value_or(0)) : 0;
    for (int i = 0; i < rtn_count && reader.remaining() >= 2; ++i) reader.u2();
    for (int i = 0; i < rtn_count && reader.remaining(); ++i) reader.u1();
    for (int i = 0; i < pgm_count && reader.remaining() >= 2; ++i) reader.u2();
    for (int i = 0; i < pgm_count && reader.remaining(); ++i) reader.u1();
    if (reader.remaining()) reader.dn();
    if (reader.remaining()) reader.cn();
    if (reader.remaining()) reader.cn();
    if (reader.remaining()) reader.cn();
    const auto test_text = reader.remaining() ? reader.cn() : "";
    if (reader.remaining()) reader.cn();
    if (reader.remaining()) reader.cn();
    const auto result_text = reader.remaining() ? reader.cn() : "";
    if (!should_include_test(*test_num, test_text)) return;
    auto& context = get_context(head, site);
    ColumnId column_id = column_registry_.get_or_create_test_column(*test_num, test_text, 0, CellValue::String);
    const auto& column = column_registry_.meta(column_id).name;
    if (collect_long_) {
      // FTR: test_flag bit 0 = fail; no numeric limits
      bool fail_flag = compute_is_fail(test_flag, std::nullopt, 0.0, 0.0, false);
      context.tests.push_back({base_long_row(*test_num, result_text, fail_flag,
          test_flag ? static_cast<std::int32_t>(*test_flag) : 0,
          opt_flag ? static_cast<std::int32_t>(*opt_flag) : 0, "FTR", -1, column_id)});
    }
    if (use_catalog_vector_)
      update_catalog_by_id(column_id, *test_num, "FTR", test_text, CellValue::null(), CellValue::null(), "");
    else {
      const auto& col_name = column_registry_.meta(column_id).name;
      update_catalog(*test_num, col_name, "FTR", test_text, CellValue::null(), CellValue::null(), "");
    }
  }

  void parse_prr(PayloadReader& reader) {
    ++deepyield_stdf::g_perf().prr_count;
    const auto head = reader.u1();
    const auto site = reader.u1();
    const auto part_flag = reader.u1();
    reader.u2();
    const auto hard_bin = reader.u2();
    const auto soft_bin = reader.u2();
    const auto x_coord = reader.i2();
    const auto y_coord = reader.i2();
    const auto test_time = reader.u4();
    const auto part_id = reader.cn();
    const auto part_text = reader.cn();
    reader.bn();
    auto& context = get_context(head, site);
    const auto pass_fail = pass_fail_from_part_flag(part_flag);
    const std::string name = !part_id.empty() ? part_id : current_lot_id_ + "_" + current_wafer_id_ + "_" + std::to_string(x_coord.value_or(0)) + "_" + std::to_string(y_coord.value_or(0)) + "_" + std::to_string(site.value_or(0)) + "_" + std::to_string(context.part_index);
    // Phase 7C: sparse default — write meta only to sparse_values
    if (context.use_sparse) {
      context.sparse_values[fc_.col_name] = CellValue::string(name);
      context.sparse_values[fc_.col_lot_id] = CellValue::string(context.lot_id.empty() ? current_lot_id_ : context.lot_id);
      context.sparse_values[fc_.col_wafer_id] = CellValue::string(context.wafer_id.empty() ? current_wafer_id_ : context.wafer_id);
      context.sparse_values[fc_.col_site] = cell_int32(site);
      context.sparse_values[fc_.col_die_x] = cell_int32(x_coord);
      context.sparse_values[fc_.col_die_y] = cell_int32(y_coord);
      context.sparse_values[fc_.col_hbin] = cell_int32(hard_bin);
      context.sparse_values[fc_.col_sbin] = cell_int32(soft_bin);
      context.sparse_values[fc_.col_part_id] = CellValue::string(part_id);
      context.sparse_values[fc_.col_part_index] = CellValue::int32(context.part_index);
      context.sparse_values[fc_.col_part_flag] = cell_int32(part_flag);
      context.sparse_values[fc_.col_pass_fail] = CellValue::string(pass_fail);
      context.sparse_values[fc_.col_part_txt] = CellValue::string(part_text);
      context.sparse_values[fc_.col_test_time_ms] = cell_int32(test_time);
      context.sparse_values[fc_.col_source_file] = CellValue::string(source_.string());
    } else {
      // Legacy path: write to context.values map
      context.values["Name"] = CellValue::string(name);
      context.values["LOT_ID"] = CellValue::string(context.lot_id.empty() ? current_lot_id_ : context.lot_id);
      context.values["WAFER_ID"] = CellValue::string(context.wafer_id.empty() ? current_wafer_id_ : context.wafer_id);
      context.values["SITE"] = cell_int32(site);
      context.values["DIE_X"] = cell_int32(x_coord);
      context.values["DIE_Y"] = cell_int32(y_coord);
      context.values["HBIN"] = cell_int32(hard_bin);
      context.values["SBIN"] = cell_int32(soft_bin);
      context.values["__part_id"] = CellValue::string(part_id);
      context.values["__part_index"] = CellValue::int32(context.part_index);
      context.values["__part_flag"] = cell_int32(part_flag);
      context.values["__pass_fail"] = CellValue::string(pass_fail);
      context.values["__part_txt"] = CellValue::string(part_text);
      context.values["__test_time_ms"] = cell_int32(test_time);
      context.values["__source_file"] = CellValue::string(source_.string());
    }
    const auto attempt_key = [&]() {
      ++deepyield_stdf::g_perf().attempt_key_string_builds;
      std::string key;
      key.reserve(part_id.size() + current_lot_id_.size() + current_wafer_id_.size() + 64);
      key.append(part_id.empty() ? name : part_id);
      key.push_back('|');
      key.append(current_lot_id_);
      key.push_back('|');
      key.append(current_wafer_id_);
      key.push_back('|');
      key.append(std::to_string(site.value_or(255)));
      key.push_back('|');
      key.append(std::to_string(x_coord.value_or(0)));
      key.push_back('|');
      key.append(std::to_string(y_coord.value_or(0)));
      return key;
    }();
    const int retest_index = attempt_counts_[attempt_key]++;

    // Phase 7C: sparse default — write retest fields only to sparse_values
    if (context.use_sparse) {
      context.sparse_values[fc_.col_retest_index] = CellValue::int32(retest_index);
      context.sparse_values[fc_.col_is_retest] = CellValue::boolean(retest_index > 0);
      context.sparse_values[fc_.col_attempt_key] = CellValue::string(attempt_key);
    } else {
      context.values["__retest_index"] = CellValue::int32(retest_index);
      context.values["__is_retest"] = CellValue::boolean(retest_index > 0);
      context.values["__attempt_key"] = CellValue::string(attempt_key);
    }

    // Direct Parquet: append wide_all row to writer (buffered, flushed at close)
    const std::int64_t mat_t0 = timing_enabled_ ? now_ticks() : 0;
    if (context.use_sparse) {
      // Phase 7C / 7D: append the ColumnId-keyed sparse row directly — no
      // string-keyed Row is built during parse; resolution happens at close.
      ++deepyield_stdf::g_perf().row_copy_count;
      ++deepyield_stdf::g_perf().row_move_count;
      writer_.append_wide_all_sparse(std::move(context.sparse_values));
    } else {
      ++deepyield_stdf::g_perf().row_move_count;
      writer_.append_wide_all(std::move(context.values));
    }
    if (timing_enabled_) timing_prr_materialize_ns_ += now_ticks() - mat_t0;

    // Compute stable part_index (shared by part_summary and compact long)
    int stable_part_index = 0;
    {
      auto ps_it = attempt_key_to_part_index_.find(attempt_key);
      if (ps_it != attempt_key_to_part_index_.end()) {
        stable_part_index = ps_it->second;
      } else {
        stable_part_index = ++part_index_counter_;
        attempt_key_to_part_index_[attempt_key] = stable_part_index;
      }
    }

    // Build and append part_summary row (plan §5.4 — Task 6)
    {
      Row ps_row;
      ps_row["part_index"]     = CellValue::int32(stable_part_index);
      ps_row["attempt_index"]  = CellValue::int32(retest_index);
      ps_row["lot_id"]         = CellValue::string(current_lot_id_);
      ps_row["wafer_id"]       = CellValue::string(current_wafer_id_);
      ps_row["die_x"]          = cell_int32(x_coord);
      ps_row["die_y"]          = cell_int32(y_coord);
      ps_row["site_num"]       = cell_int32(site);
      ps_row["hbin"]           = cell_int32(hard_bin);
      ps_row["sbin"]           = cell_int32(soft_bin);
      ps_row["is_final"]       = CellValue::boolean(false);  // set at close()
      ps_row["record_sequence"]= CellValue::int32(++record_sequence_);
      ps_row["__attempt_key"]  = CellValue::string(attempt_key);  // internal, stripped at close
      writer_.append_part_summary(std::move(ps_row));
    }

    // Append compact long rows — Phase 3: direct struct field assignment, no map lookup
    for (auto& test : context.tests) {
      test.long_row.part_index = stable_part_index;
      test.long_row.attempt_index = retest_index;
      writer_.append_long_fast(test.long_row);
    }
    if (atvi_enabled_) {
      emit_atvi_attempt(context, stable_part_index, retest_index, site, hard_bin,
                        soft_bin, x_coord, y_coord, part_text, pass_fail);
    }
    contexts_.erase(context_key(head, site));
  }

  bool has_test_filter() const {
    return !included_test_nums_.empty() || !included_test_names_.empty();
  }

  bool should_include_test(std::uint32_t test_num, std::string test_name) const {
    if (!has_test_filter() || included_test_nums_.count(test_num) > 0) return true;
    std::transform(test_name.begin(), test_name.end(), test_name.begin(), [](unsigned char ch) {
      return static_cast<char>(std::tolower(ch));
    });
    return included_test_names_.count(test_name) > 0;
  }

  // ---- Direct ATVI emission (plan WP3) ----

  static std::optional<std::string> text_opt(const std::string& value) {
    return value.empty() ? std::nullopt : std::optional<std::string>(value);
  }

  void emit_atvi_attempt(const PartContext& context, int stable_part_index, int retest_index,
                         std::optional<std::uint8_t> site,
                         std::optional<std::uint16_t> hard_bin,
                         std::optional<std::uint16_t> soft_bin,
                         std::optional<std::int16_t> x_coord,
                         std::optional<std::int16_t> y_coord, const std::string& part_text,
                         const std::string& pass_fail) {
    if (!atvi_writer_) return;
    deepyield_stdf::AtviAttempt attempt;
    attempt.source_file_hash = source_file_hash_;
    attempt.source_record_id = source_file_hash_ + ":" + std::to_string(record_sequence_);
    attempt.part_index = stable_part_index;
    attempt.attempt_index = retest_index;
    attempt.source_record_sequence = record_sequence_;
    attempt.project_type = atvi_project_type_;
    attempt.test_stage =
        (atvi_project_type_ == "EWS" || atvi_project_type_ == "FT")
            ? std::optional<std::string>(atvi_project_type_)
            : std::nullopt;
    const std::string lot = current_lot_id_;
    const std::string wafer = current_wafer_id_;
    if (atvi_project_type_ == "EWS" && !lot.empty() && !wafer.empty() &&
        x_coord.has_value() && y_coord.has_value()) {
      attempt.chip_uid = "EWS|" + lot + "|" + wafer + "|" + std::to_string(*x_coord) +
                         "|" + std::to_string(*y_coord);
      attempt.identity_resolution = "RESOLVED";
    } else {
      attempt.identity_resolution = "PARTIAL";
      attempt.identity_reason_codes = {"IDENTITY_UNAVAILABLE"};
    }
    attempt.lot_id = text_opt(lot);
    attempt.wafer_id = text_opt(wafer);
    attempt.site = site.has_value() ? std::optional<std::string>(std::to_string(*site))
                                    : std::nullopt;
    attempt.die_x = x_coord.has_value()
                        ? std::optional<std::int32_t>(*x_coord)
                        : std::nullopt;
    attempt.die_y = y_coord.has_value()
                        ? std::optional<std::int32_t>(*y_coord)
                        : std::nullopt;
    attempt.part_txt = text_opt(part_text);
    attempt.hbin = hard_bin.has_value()
                       ? std::optional<std::int32_t>(*hard_bin)
                       : std::nullopt;
    attempt.sbin = soft_bin.has_value()
                       ? std::optional<std::int32_t>(*soft_bin)
                       : std::nullopt;
    if (mir_start_time_.has_value()) {
      attempt.test_start_time_us =
          static_cast<std::int64_t>(*mir_start_time_) * 1000000LL;
    }
    if (mrr_finish_time_.has_value()) {
      attempt.test_end_time_us =
          static_cast<std::int64_t>(*mrr_finish_time_) * 1000000LL;
    }
    attempt.program_version = text_opt(program_revision_);
    attempt.program_version_source =
        attempt.program_version.has_value() ? "ASSET_HEADER" : "UNAVAILABLE";
    attempt.temperature_source = "UNAVAILABLE";
    std::string result = pass_fail.empty() ? "UNKNOWN" : pass_fail;
    if (result != "PASS" && result != "FAIL" && result != "ABORT") result = "UNKNOWN";
    attempt.device_result = result;
    attempt.source_evidence_version = "cpp-direct-v1";
    attempt.mapping_candidate_capture_state = "UNAVAILABLE_LEGACY";
    atvi_writer_->add_attempt(attempt);

    deepyield_stdf::AtviMappingEvidence evidence;
    evidence.source_file_hash = source_file_hash_;
    evidence.source_record_id = attempt.source_record_id;
    evidence.part_index = stable_part_index;
    evidence.attempt_index = retest_index;
    evidence.evidence_kind = "IDENTITY";
    evidence.candidate_index = 0;
    evidence.evidence_origin = "IDENTITY_VALUES";
    evidence.lot_tag = "NULL";
    evidence.wafer_tag = "NULL";
    evidence.die_x_tag = "NULL";
    evidence.die_y_tag = "NULL";
    evidence.policy_input_version = "candidate-framing-v1";
    atvi_writer_->add_mapping_evidence(evidence);

    auto catalog_double = [&](const Row& row, const std::string& key) -> std::optional<double> {
      auto it = row.find(key);
      if (it == row.end() || it->second.is_null() || it->second.type != CellValue::Double) {
        return std::nullopt;
      }
      return it->second.dbl_val;
    };
    auto catalog_text = [&](const Row& row, const std::string& key) -> std::optional<std::string> {
      auto it = row.find(key);
      if (it == row.end() || it->second.is_null() || it->second.type != CellValue::String) {
        return std::nullopt;
      }
      return text_opt(it->second.str_val);
    };
    const auto emit_t0 = std::chrono::steady_clock::now();
    for (const auto& test : context.tests) {
      const auto& row = test.long_row;
      if (row.record_type == "FTR" || row.column_id >= catalog_vec_.size()) continue;
      const Row& catalog = catalog_vec_[row.column_id];
      deepyield_stdf::AtviObservation obs;
      obs.observation_id = deepyield_stdf::observation_id_native(
          source_file_hash_, stable_part_index, retest_index, row.record_sequence,
          row.record_type, static_cast<std::uint32_t>(row.test_num),
          row.result_index >= 0 ? std::optional<std::int32_t>(row.result_index)
                                : std::nullopt);
      obs.observation_source = "NATIVE_RECORD";
      obs.source_file_hash = source_file_hash_;
      obs.part_index = stable_part_index;
      obs.attempt_index = retest_index;
      obs.source_record_sequence = row.record_sequence;
      obs.record_type = row.record_type;
      obs.test_num = static_cast<std::uint32_t>(row.test_num);
      if (row.result_index >= 0) obs.result_index = row.result_index;
      obs.test_item_key = row.test_item_key;
      obs.column_name = catalog_text(catalog, "test_id");
      obs.test_name = catalog_text(catalog, "name");
      obs.unit = catalog_text(catalog, "unit");
      obs.lsl = catalog_double(catalog, "low_limit");
      obs.usl = catalog_double(catalog, "high_limit");
      char* end = nullptr;
      const std::string result_text = row.result;
      double parsed = 0.0;
      bool parsed_ok = !result_text.empty();
      if (parsed_ok) {
        parsed = std::strtod(result_text.c_str(), &end);
        parsed_ok = (end != nullptr && *end == '\0');
      }
      obs.result_value = parsed_ok ? std::optional<double>(parsed) : std::nullopt;
      obs.value_state = deepyield_stdf::value_state_of(obs.result_value);
      std::optional<std::uint8_t> tf =
          (row.test_flag >= 0 && row.test_flag <= 255)
              ? std::optional<std::uint8_t>(static_cast<std::uint8_t>(row.test_flag))
              : std::nullopt;
      std::optional<std::uint8_t> pf =
          (row.param_flag >= 0 && row.param_flag <= 255)
              ? std::optional<std::uint8_t>(static_cast<std::uint8_t>(row.param_flag))
              : std::nullopt;
      auto classification = deepyield_stdf::classify_test_item_outcome(
          tf, pf, obs.result_value, obs.lsl, obs.usl, false, false, false);
      obs.test_outcome = classification.test_outcome;
      obs.outcome_evidence = classification.outcome_evidence;
      obs.test_flag = tf;
      obs.param_flag = pf;
      obs.lot_id = attempt.lot_id;
      obs.wafer_id = attempt.wafer_id;
      obs.site = attempt.site;
      obs.die_x = attempt.die_x;
      obs.die_y = attempt.die_y;
      obs.part_txt = attempt.part_txt;
      obs.test_stage = attempt.test_stage;
      obs.program_version = attempt.program_version;
      obs.test_start_time_us = attempt.test_start_time_us;
      obs.test_end_time_us = attempt.test_end_time_us;
      atvi_writer_->add_observation(obs);
    }
    atvi_emit_ticks_ += std::chrono::duration_cast<std::chrono::microseconds>(
                            std::chrono::steady_clock::now() - emit_t0)
                            .count();
  }

  fs::path source_;
  ParquetAssetWriter& writer_;
  bool collect_long_ = true;
  bool little_endian_ = true;
  std::string current_lot_id_;
  std::string current_wafer_id_;
  std::string part_type_;         // MIR.PART_TYP → device
  std::string program_name_;      // MIR.JOB_NAM
  std::string program_revision_;

  // SDR site_context
  std::map<SiteContextKey, SdrSiteInfo> site_context_;
  int sdr_record_count_ = 0;
  std::optional<std::uint32_t> mir_setup_time_;
  std::optional<std::uint32_t> mir_start_time_;
  std::optional<std::uint32_t> mrr_finish_time_;
  std::optional<std::uint32_t> wrr_finish_time_;
  int segment_ordinal_ = 1;
  bool segment_started_ = false;
  std::string mrr_user_text_;
  int part_counter_ = 0;
  int part_index_counter_ = 0;  // stable part_index across retests (plan §5.4 — Task 6)
  int record_sequence_ = 0;     // part-level record-order counter (plan §5.4)
  int long_record_sequence_ = 0; // test-level record-order counter (plan §6.1 — Task 7)
  std::unordered_map<ContextKey, PartContext, ContextKeyHash> contexts_;
  std::unordered_map<std::string, int> attempt_counts_;
  std::unordered_map<std::string, int> attempt_key_to_part_index_;  // stable part_index per die
  std::map<std::string, int> record_counts_;
  std::vector<std::string> warnings_;
  std::map<std::string, Row> catalog_;            // legacy string-key map
  // Phase 7B: ColumnId-indexed catalog vector
  std::vector<Row> catalog_vec_;
  std::vector<std::uint8_t> catalog_seen_;
  bool use_catalog_vector_ = true;
  bool atvi_enabled_ = false;
  std::string atvi_project_type_ = "UNKNOWN";
  std::string source_file_hash_;
  std::unique_ptr<deepyield_stdf::AtviWriter> atvi_writer_;
  std::int64_t atvi_emit_ticks_ = 0;
  deepyield_stdf::ColumnRegistry column_registry_;
  FixedColumnIds fc_;
  bool use_sparse_row_ = true;   // Phase 7C: default sparse path
  // Reusable record payload buffer (avoids one heap allocation per STDF record).
  std::vector<std::uint8_t> record_buf_;
  // test_num → ColumnId fast cache (column ids are stable per test_num;
  // avoids a registry call + atomic counter increment on every PTR).
  std::vector<deepyield_stdf::ColumnId> test_col_by_num_;
  std::unordered_set<std::uint32_t> included_test_nums_;
  std::unordered_set<std::string> included_test_names_;
  // Coarse parse-loop timing (ns); enabled via DEEPYIELD_PARSE_TIMING=1.
  bool timing_enabled_ = false;
  std::int64_t timing_io_ns_ = 0;            // read_header + payload read
  std::int64_t timing_parse_record_ns_ = 0;  // parse_record total
  std::int64_t timing_ptr_ns_ = 0;           // parse_ptr
  std::int64_t timing_ptr_registry_ns_ = 0;  // get_or_create_test_column
  std::int64_t timing_ptr_sparse_ns_ = 0;    // sparse_values insert
  std::int64_t timing_ptr_catalog_ns_ = 0;   // update_catalog
  std::int64_t timing_prr_ns_ = 0;           // parse_prr
  std::int64_t timing_prr_materialize_ns_ = 0;  // sparse→Row + append_wide_all
  std::int64_t timing_other_records_ns_ = 0;    // non-PTR/PRR records
  deepyield_stdf::ProgressEmitter* progress_ = nullptr;
};

// ---------- Helpers ----------

struct ScopedFileCleanup {
  fs::path path;
  bool active = true;
  ~ScopedFileCleanup() {
    if (active && !path.empty()) {
      std::error_code ignored;
      fs::remove(path, ignored);
    }
  }
};

void print_help() {
  std::cout << "stdf2parquet " << kParserVersion << "\n"
            << "features: direct-parquet-row-groups\n\n"
            << "Usage: stdf2parquet --input <file.stdf[.bz2]> --out-dir <asset_dir>\n"
            << "                  [--profile overview|materialize|full-debug]\n"
            << "                  [--mode fast|full|details]\n"
            << "                  [--atvi-dir <generation_dir> --atvi-project EWS|FT]\n"
            << "                  [--atvi-source-hash <sha256>]\n"
            << "                  [--include-test-num <number>] [--include-test-name <exact-name>]\n\n"
            << "Profiles (plan §10 — one-step full assetization):\n"
            << "  overview     Stage 1: wide.parquet + test_catalog.parquet + manifest.json (fast, overview-ready)\n"
            << "  materialize  Stage 2: wide_all.parquet + long.parquet + part_summary.parquet (requires overview first)\n"
            << "  full-debug   Legacy full debug mode (all tables in one pass; not the default path)\n\n"
            << "--mode is retained for backward compatibility; --profile takes precedence.\n"
            << "Test filters may be repeated and are supported only by overview/fast mode.\n";
}

int run_python_helper(const std::string& args) {
  const std::string command = python_command() + " -m deepyield.io.stdf_parquet_writer " + args;
  const std::string wrapped = system_command(command);
  return std::system(wrapped.c_str());
}

}  // namespace

// Map --profile choice to internal mode string (plan §10).
// overview   → "fast"    (wide + test_catalog only; Stage 1)
// materialize→ "full"    (all tables; Stage 2)
// full-debug → "details" (legacy full-debug; not the default path)
std::string profile_to_mode(const std::string& profile) {
  if (profile == "overview")   return "fast";
  if (profile == "materialize") return "full";
  if (profile == "full-debug")  return "details";
  return "";  // unrecognized
}

bool is_valid_profile(const std::string& profile) {
  return profile == "overview" || profile == "materialize" || profile == "full-debug";
}

int main(int argc, char** argv) {
  if (argc == 3 && (std::string(argv[1]) == "--release-ipc" ||
                    std::string(argv[1]) == "--release-calculation-ipc")) {
    return deepyield_stdf::release::main(argv[2], std::string(argv[1]) == "--release-calculation-ipc");
  }
  std::string input_path;
  std::string out_dir;
  std::string mode = "full";
  std::string profile;  // --profile (plan §10); empty means not set
  std::string atvi_dir;          // --atvi-dir: direct ATVI generation output
  std::string atvi_project;      // --atvi-project: EWS / FT / UNKNOWN
  std::string atvi_source_hash;  // --atvi-source-hash: 64-hex source SHA-256
  std::unordered_set<std::uint32_t> included_test_nums;
  std::unordered_set<std::string> included_test_names;
  deepyield_stdf::DebugConfig debug_cfg;
  int debug_arrow_schema_n = -1;
  for (int index = 1; index < argc; ++index) {
    std::string arg = argv[index];
    if (arg == "--help" || arg == "-h") {
      print_help();
      return 0;
    }
    if ((arg == "--input" || arg == "-i") && index + 1 < argc) {
      input_path = argv[++index];
      continue;
    }
    if ((arg == "--out-dir" || arg == "--out" || arg == "-o") && index + 1 < argc) {
      out_dir = argv[++index];
      continue;
    }
    if (arg == "--profile" && index + 1 < argc) {
      profile = argv[++index];
      std::transform(profile.begin(), profile.end(), profile.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
      continue;
    }
    if (arg == "--mode" && index + 1 < argc) {
      mode = argv[++index];
      std::transform(mode.begin(), mode.end(), mode.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
      continue;
    }
    if (arg == "--atvi-dir" && index + 1 < argc) {
      atvi_dir = argv[++index];
      continue;
    }
    if (arg == "--atvi-project" && index + 1 < argc) {
      atvi_project = argv[++index];
      std::transform(atvi_project.begin(), atvi_project.end(), atvi_project.begin(),
                     [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
      continue;
    }
    if (arg == "--atvi-source-hash" && index + 1 < argc) {
      atvi_source_hash = argv[++index];
      continue;
    }
    if (arg == "--include-test-num" && index + 1 < argc) {
      try {
        std::size_t consumed = 0;
        const std::string value = argv[++index];
        const auto parsed = std::stoul(value, &consumed);
        if (consumed != value.size() || parsed > UINT32_MAX) {
          throw std::out_of_range("test number");
        }
        included_test_nums.insert(static_cast<std::uint32_t>(parsed));
      } catch (const std::exception&) {
        std::cerr << "Invalid --include-test-num value. Expected an unsigned 32-bit integer.\n";
        return 2;
      }
      continue;
    }
    if (arg == "--include-test-name" && index + 1 < argc) {
      included_test_names.insert(argv[++index]);
      continue;
    }
    // Debug flags (no input file needed for --debug-arrow-schema)
    if (arg == "--debug-arrow-schema" && index + 1 < argc) {
      debug_arrow_schema_n = std::stoi(argv[++index]);
      continue;
    }
    if (arg == "--debug-stop-after-schema") {
      debug_cfg.stop_after_schema = true;
      continue;
    }
    if (arg == "--debug-schema-prefix" && index + 1 < argc) {
      debug_cfg.schema_prefix = std::stoi(argv[++index]);
      continue;
    }
    if (arg == "--debug-stop-after-builders") {
      debug_cfg.stop_after_builders = true;
      continue;
    }
    if (arg == "--debug-stop-after-append") {
      debug_cfg.stop_after_append = true;
      continue;
    }
    if (arg == "--debug-stop-after-finish-arrays") {
      debug_cfg.stop_after_finish_arrays = true;
      continue;
    }
    if (arg == "--debug-stop-after-recordbatch") {
      debug_cfg.stop_after_recordbatch = true;
      continue;
    }
    if (arg == "--perf-log") {
      deepyield_stdf::set_perf_log_enabled(true);
      continue;
    }
    if (arg == "--debug-trace") {
      deepyield_stdf::Logger::instance().set_level(deepyield_stdf::LogLevel::Trace);
      deepyield_stdf::trace::set_trace_enabled(true);
      continue;
    }
    if (arg == "--verbose") {
      deepyield_stdf::Logger::instance().set_level(deepyield_stdf::LogLevel::Info);
      continue;
    }
    if (arg == "--strict-append-validate") {
      // Handled in Phase 5 — recognized here for forward compat
      continue;
    }
    if (arg == "--long-writer" && index + 1 < argc) {
      std::string mode = argv[++index];
      std::transform(mode.begin(), mode.end(), mode.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
      // Set on writer after construction
      continue;
    }
    if (arg == "--legacy-parser") {
      // Phase 4+ rollback: force legacy row storage
      continue;
    }
    if (arg == "--disable-catalog-vector") {
      // Phase 7B rollback: use legacy string-key catalog map
      continue;
    }
    if (arg == "--optimized-parser") {
      // Phase 4+: enable column-id row storage (sparse row)
      continue;
    }
    if (arg == "--row-storage" && index + 1 < argc) {
      std::string storage = argv[++index];
      std::transform(storage.begin(), storage.end(), storage.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
      if (storage == "column-id") {
        // handled after parser construction
      }
      continue;
    }
    if (!arg.empty() && arg[0] != '-' && input_path.empty()) {
      input_path = arg;
      continue;
    }
  }
  // Print env diagnostics first thing
  deepyield_stdf::print_env_diagnostics(argc, argv);

  // Standalone Arrow schema smoke test (no input file needed)
  if (debug_arrow_schema_n > 0) {
    return deepyield_stdf::debug_arrow_schema_test(debug_arrow_schema_n);
  }

  if (input_path.empty() || out_dir.empty()) {
    print_help();
    return 2;
  }

  // --profile takes precedence over --mode (plan §10)
  if (!profile.empty()) {
    if (!is_valid_profile(profile)) {
      std::cerr << "Unsupported profile: " << profile
                << ". Use overview, materialize, or full-debug.\n";
      return 2;
    }
    mode = profile_to_mode(profile);
    // Mixed --profile + --mode is allowed; --profile always wins.
  }

  // Emit the resolved profile/mode so downstream tooling can inspect it
  std::cerr << "[stdf2parquet] profile=" << (profile.empty() ? "(none)" : profile)
            << " mode=" << mode << "\n";
  if (mode != "fast" && mode != "full" && mode != "details") {
    std::cerr << "Unsupported mode: " << mode << "\n";
    return 2;
  }
  if ((!included_test_nums.empty() || !included_test_names.empty()) && mode != "fast") {
    std::cerr << "Test filters are supported only by overview/fast mode.\n";
    return 2;
  }

  try {
    const fs::path source = fs::absolute(input_path);
    const fs::path output = fs::absolute(out_dir);
    fs::create_directories(output);
    std::map<std::string, double> timings;

    // Direct Parquet pipeline. Fast mode defers heavy detail tables so the
    // current UI can use the final wide asset sooner.
    // --profile overview  → mode=fast  + overview_ready manifest
    // --profile materialize → mode=full + fully_materialized manifest
    // --profile full-debug  → mode=details (legacy)
    const bool write_detail_tables = mode != "fast";
    ParquetAssetWriter writer(output, 0, write_detail_tables, write_detail_tables);

    // Phase 3 of long-table optimization: default to fast path
    // Check for --long-writer legacy override
    {
      for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--long-writer" && i + 1 < argc) {
          std::string mode = argv[i + 1];
          std::transform(mode.begin(), mode.end(), mode.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
          if (mode == "legacy") {
            writer.set_long_writer_mode("legacy");
            DY_LOG_INFO("[stdf2parquet] long-writer=legacy (fallback)");
          }
          break;
        }
      }
    }

    // ---- Profile-specific manifest status (plan §11 — Task 5+9) ----
    if (profile == "overview") {
      writer.set_manifest_status("overview_ready");
    } else if (profile == "materialize" || (profile.empty() && mode == "full")) {
      writer.set_manifest_status("fully_materialized");
    }

    // ---- Progress emitter (Step 9) ----
    deepyield_stdf::ProgressEmitter progress(std::cout);

    writer.open();

    // Wire debug configuration for crash isolation
    writer.set_debug_config(debug_cfg);

    DY_LOG_INFO("[stage] parse_start");
    const auto parse_start = std::chrono::steady_clock::now();
    StdfParser parser(source, writer, write_detail_tables, &progress);
    parser.set_test_filter(included_test_nums, included_test_names);

    // Phase 7B: Disable catalog vector if --disable-catalog-vector
    {
      for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--disable-catalog-vector") {
          parser.set_use_catalog_vector(false);
          DY_LOG_INFO("[stdf2parquet] catalog vector disabled, using legacy map");
          break;
        }
      }
    }

    // Phase 4: Enable sparse row storage if --optimized-parser or --row-storage column-id
    {
      bool use_sparse = false;
      for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--optimized-parser") { use_sparse = true; break; }
        if (arg == "--row-storage" && i + 1 < argc) {
          std::string storage = argv[i + 1];
          std::transform(storage.begin(), storage.end(), storage.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
          if (storage == "column-id") { use_sparse = true; break; }
        }
      }
      if (use_sparse) {
        parser.set_use_sparse_row(true);
      }
    }

    // Direct ATVI generation (plan WP3): only in full/materialize mode.
    if (!atvi_dir.empty()) {
      const bool is_materialize_main =
          (profile == "materialize" || (profile.empty() && mode == "full"));
      if (is_materialize_main) {
        std::string source_hash = atvi_source_hash;
        if (source_hash.empty()) {
          std::ifstream in(source, std::ios::binary);
          std::string data((std::istreambuf_iterator<char>(in)),
                           std::istreambuf_iterator<char>());
          source_hash = deepyield_stdf::Sha256::hex_digest(data);
        }
        parser.enable_atvi(fs::path(atvi_dir), atvi_project, source_hash);
        DY_LOG_INFO("[stdf2parquet] ATVI direct writer enabled -> " + atvi_dir);
      } else {
        fs::create_directories(atvi_dir);
        std::ofstream pending(fs::path(atvi_dir) / "manifest.json");
        pending << "{\"status\":\"PENDING\",\"reason\":\"profile is not "
                   "materialize; ATVI requires full/materialize mode\"}";
        DY_LOG_INFO("[stdf2parquet] ATVI marked PENDING for non-materialize profile");
      }
    }

    if (ends_with_ci(source.string(), ".bz2")) {
      // Wire bz2 progress into the emitter
      deepyield_stdf::Bz2Progress bz2_progress;
      const auto bz2_open_start = std::chrono::steady_clock::now();
      auto bz2_stream = deepyield_stdf::open_bz2_input_stream(source.string(), &bz2_progress);
      if (!bz2_stream) {
        throw std::runtime_error("Unable to open bzip2 input stream: " + source.string());
      }
      timings["bz2_stream_open_seconds"] = elapsed_seconds(bz2_open_start);

      // Wire bz2 progress counters into the emitter for live progress
      progress.set_compressed_counters(
          bz2_progress.compressed_bytes_read,
          bz2_progress.compressed_bytes_total);

      progress.emit_stage("decompressing");
      parser.parse(*bz2_stream);
    } else {
      parser.parse(source);
    }
    timings["cpp_parse_parquet_seconds"] = elapsed_seconds(parse_start);
    std::cerr << "[stdf2parquet] ATVI emit ticks=" << parser.atvi_emit_ticks() << "\n";

    std::cerr << "[stage] parse_done"
              << " ptr_count=" << parser.record_count("PTR")
              << " prr_count=" << parser.record_count("PRR")
              << " ftr_count=" << parser.record_count("FTR")
              << " mpr_count=" << parser.record_count("MPR")
              << std::endl;

    const auto parsed_records = parser.record_count("FAR") + parser.record_count("MIR") +
        parser.record_count("MRR") + parser.record_count("PIR") + parser.record_count("PRR") +
        parser.record_count("PTR") + parser.record_count("MPR") + parser.record_count("FTR") +
        parser.record_count("SDR") + parser.record_count("WIR") + parser.record_count("WRR");
    if (parsed_records == 0) {
      throw std::runtime_error("EMPTY_STDF_NO_RECORDS");
    }

    // Emit close-stage progress (Step 9)
    progress.emit_stage("writing_wide", "building final-only wide table");

    const auto manifest_start = std::chrono::steady_clock::now();
    // All test columns are discovered by now; hand the ColumnId → name map to
    // the writer so the ColumnId-keyed wide rows can be resolved at close.
    parser.propagate_column_info();
    parser.finalize_and_close(source, /*file_hash=*/"");
    parser.finish_atvi();
    timings["parser_manifest_seconds"] = elapsed_seconds(manifest_start);

    // Emit final progress before manifest check (Step 9)
    progress.emit_stage("finalizing", "verifying outputs");

    // ---- Measure output file sizes (Tasks 5+9) ----
    double wide_size_mb = 0.0, wide_all_size_mb = 0.0;
    double long_size_mb = 0.0, part_summary_size_mb = 0.0;
    {
      std::error_code ec;
      auto sz = fs::file_size(output / "parquet" / "wide.parquet", ec);
      if (!ec) wide_size_mb = static_cast<double>(sz) / (1024.0 * 1024.0);
      sz = fs::file_size(output / "parquet" / "wide_all.parquet", ec);
      if (!ec) wide_all_size_mb = static_cast<double>(sz) / (1024.0 * 1024.0);
      sz = fs::file_size(output / "parquet" / "long.parquet", ec);
      if (!ec) long_size_mb = static_cast<double>(sz) / (1024.0 * 1024.0);
      sz = fs::file_size(output / "parquet" / "part_summary.parquet", ec);
      if (!ec) part_summary_size_mb = static_cast<double>(sz) / (1024.0 * 1024.0);
    }

    // ---- Materialize profile post-processing (plan §9, §12 — Task 9) ----
    bool is_materialize = (profile == "materialize" || (profile.empty() && mode == "full"));
    if (is_materialize) {
      // Verify all three materialize output files exist
      bool wa_ok = fs::exists(output / "parquet" / "wide_all.parquet");
      bool lo_ok = fs::exists(output / "parquet" / "long.parquet");
      bool ps_ok = fs::exists(output / "parquet" / "part_summary.parquet");
      if (!wa_ok || !lo_ok || !ps_ok) {
        std::cerr << "[stdf2parquet] ERROR: materialize profile but output(s) missing:"
                  << (!wa_ok ? " wide_all" : "")
                  << (!lo_ok ? " long" : "")
                  << (!ps_ok ? " part_summary" : "") << "\n";
        throw std::runtime_error("FULL_STDF_V2_OUTPUTS_MISSING");
      }

      // Update manifest.json atomically (plan §11 — Task 9).
      // Re-read the manifest written by finalize_and_close, ensure all table
      // readiness flags are correct, and write back via .tmp → rename.
      {
        auto manifest_path = output / "manifest.json";
        if (fs::exists(manifest_path)) {
          // Read current manifest
          auto manifest_text = [&]() -> std::string {
            std::ifstream in(manifest_path, std::ios::binary);
            if (!in) return "{}";
            std::ostringstream buf;
            buf << in.rdbuf();
            return buf.str();
          }();

          // Patch fully_materialized flag + table readiness
          std::string patched;
          auto fm_pos = manifest_text.find("\"fully_materialized\":");
          if (fm_pos != std::string::npos) {
            auto comma_pos = manifest_text.find(",", fm_pos);
            if (comma_pos == std::string::npos) {
              throw std::runtime_error("Invalid manifest JSON: cannot patch fully_materialized — no comma after value");
            }
            patched = manifest_text.substr(0, fm_pos)
                + "\"fully_materialized\":true,"
                + manifest_text.substr(comma_pos + 1);
          } else {
            // Insert before the first comma after status
            auto st_pos = manifest_text.find("\"status\":\"fully_materialized\"");
            if (st_pos != std::string::npos) {
              auto comma_pos = manifest_text.find(",", st_pos);
              if (comma_pos != std::string::npos) {
                patched = manifest_text.substr(0, comma_pos + 1)
                    + "\"fully_materialized\":true,"
                    + manifest_text.substr(comma_pos + 1);
              }
            }
          }
          if (patched.empty()) patched = manifest_text;

          // Write atomically: .tmp → rename
          auto tmp_path = output / "manifest.json.tmp";
          {
            std::ofstream out(tmp_path, std::ios::binary);
            if (out) out << patched;
          }
          if (fs::exists(tmp_path)) {
            std::error_code ec;
            fs::rename(tmp_path, manifest_path, ec);
            if (ec) {
              fs::copy(tmp_path, manifest_path, ec);
              fs::remove(tmp_path, ec);
            }
          }
        }
      }
    }

    // ---- Write parser_timing.json in v2 format (plan §12 — Tasks 5+9) ----
    {
      double overview_total = timings["cpp_parse_parquet_seconds"] + timings["parser_manifest_seconds"];
      if (timings.count("bz2_stream_open_seconds")) overview_total += timings["bz2_stream_open_seconds"];

      // Materialize stage timing (from writer, valid after close)
      double mat_write_wa  = is_materialize ? writer.write_wide_all_sec() : 0.0;
      double mat_write_lo  = is_materialize ? writer.write_long_sec() : 0.0;
      double mat_write_ps  = is_materialize ? writer.write_part_summary_sec() : 0.0;
      double mat_total     = mat_write_wa + mat_write_lo + mat_write_ps;
      double total_sec     = overview_total + mat_total;

      // Write atomically: timing.json.tmp → rename
      auto timing_tmp = output / "parser_timing.json.tmp";
      auto timing_final = output / "parser_timing.json";
      {
        std::ofstream out(timing_tmp, std::ios::binary);
        if (!out) throw std::runtime_error("Unable to write parser_timing.json");
        out << "{"
            << "\"schema_version\":2,"
            << "\"overview\":{"
            << "\"parse_sec\":" << std::fixed << std::setprecision(6) << timings["cpp_parse_parquet_seconds"] << ","
            << "\"write_wide_sec\":" << timings["parser_manifest_seconds"] << ","
            << "\"output_wide_size_mb\":" << wide_size_mb << ","
            << "\"test_item_count\":" << writer.catalog_count() << ","
            << "\"final_part_count\":" << writer.wide_count() << ","
            << "\"total_sec\":" << overview_total
            << "},"
            << "\"materialize\":{"
            << "\"parse_sec\":0,"
            << "\"write_wide_all_sec\":" << mat_write_wa << ","
            << "\"write_long_sec\":" << mat_write_lo << ","
            << "\"write_part_summary_sec\":" << mat_write_ps << ","
            << "\"output_wide_all_size_mb\":" << wide_all_size_mb << ","
            << "\"output_long_size_mb\":" << long_size_mb << ","
            << "\"output_part_summary_size_mb\":" << part_summary_size_mb << ","
            << "\"device_attempt_count\":" << (is_materialize ? writer.wide_all_count() : 0) << ","
            << "\"total_sec\":" << mat_total
            << "},"
            << "\"peak_memory_mb\":0,"
            << "\"total_sec\":" << total_sec
            << "}\n";
      }
      // Atomic rename
      {
        std::error_code ec;
        fs::rename(timing_tmp, timing_final, ec);
        if (ec) {
          fs::copy(timing_tmp, timing_final, ec);
          fs::remove(timing_tmp, ec);
        }
      }
    }

    // ---- Emit completed event (Step 9) ----
    progress.emit_completed(
        static_cast<std::uint64_t>(writer.wide_count()));

    // ---- Emit perf counters if enabled (Phase 0) ----
    if (deepyield_stdf::perf_log_enabled()) {
      deepyield_stdf::write_perf_log(std::cerr, deepyield_stdf::g_perf());
    }

    // ---- Emit coarse parse-loop timing if enabled (diagnostic) ----
    parser.emit_parse_timing(std::cerr);

    std::cout << "manifest=" << (output / "manifest.json").string() << "\n";
    return 0;
  } catch (const std::exception& exc) {
    // Emit error event (Step 9)
    try {
      deepyield_stdf::ProgressEmitter err_progress(std::cout);
      err_progress.emit_error("fatal", exc.what());
    } catch (...) {}
    std::cerr << exc.what() << "\n";

    // Map specific error markers to exit codes for crash isolation
    std::string err = exc.what();
    if (err.find("[schema-check]") != std::string::npos) return 31;
    if (err.find("[append-type-mismatch]") != std::string::npos) return 41;
    if (err.find("[array-length-mismatch]") != std::string::npos) return 42;

    return 1;
  }
}
