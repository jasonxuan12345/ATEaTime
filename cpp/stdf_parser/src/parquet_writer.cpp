#include "deepyield_stdf/parquet_writer.hpp"
#include "deepyield_stdf/logger.hpp"
#include "deepyield_stdf/perf_counters.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <iomanip>
#include <initializer_list>
#include <set>
#include <sstream>
#include <typeinfo>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
#include <stdexcept>
#include <unordered_map>

#include <arrow/api.h>
#include <arrow/io/api.h>
#include <parquet/arrow/writer.h>
#include <parquet/properties.h>

namespace deepyield_stdf {

namespace {

constexpr const char* kParserVersion = "deepyield-stdf-cpp-0.3.0";
constexpr const char* kSchemaVersion = "dy_stdf_parquet_v1";

// ---------- debug configuration (set from CLI before open/close) ----------

DebugConfig g_debug;

// ---------- forward declarations ----------

bool validate_fields_before_schema(const arrow::FieldVector& fields);

// ---------- fixed column orders ----------

}  // namespace

// Compact long columns (plan §6.1 — Task 7).
// Metadata fields (lot_id, wafer_id, die_x, die_y, test_name, unit, lsl, usl)
// are stored in part_summary.parquet and test_catalog.parquet respectively.
const std::vector<std::string> LONG_COLUMNS = {
    "part_index",
    "attempt_index",
    "test_num",
    "result",
    "is_fail",
    "test_flag",
    "param_flag",
    "record_sequence",
    "record_type",
    "result_index",
    "test_item_key",
};

const std::vector<std::string> CATALOG_COLUMNS = {
    "test_id", "test_num", "record_type", "name", "unit", "low_limit", "high_limit",
};

const std::vector<std::string> PART_SUMMARY_COLUMNS = {
    "part_index",
    "attempt_index",
    "lot_id",
    "wafer_id",
    "die_x",
    "die_y",
    "site_num",
    "hbin",
    "sbin",
    "is_final",
    "record_sequence",
};

namespace {

const std::vector<std::string> WIDE_IDENTITY_COLUMNS = {
    "Name", "LOT_ID", "WAFER_ID", "SITE", "DIE_X", "DIE_Y", "HBIN", "SBIN",
    "Lot_ID", "device", "part_type", "start_time", "finish_time", "test_program_name",
};

const std::vector<std::string> WIDE_META_COLUMNS = {
    "__part_id",     "__part_index",  "__part_flag",   "__pass_fail",
    "__part_txt",    "__test_time_ms","__source_file",  "__retest_index",
    "__is_retest",   "__attempt_key", "__segment_id",
};

const std::vector<std::string> FT_EXECUTION_FACT_COLUMNS = {
    "schema_version", "source_asset_id", "source_sha256", "source_file", "segment_id",
    "project_id", "product_id", "lot_id", "program_name", "program_revision",
    "start_time_utc", "finish_time_utc", "source_timezone", "total_prr", "valid_prr",
    "pass_prr", "fail_prr", "abort_prr", "unknown_prr", "hbin_counts_json",
    "sbin_counts_json", "result_source", "parser_version", "result_policy_id",
    "result_policy_status", "reason_codes_json",
};

const std::vector<std::string> EWS_ATTEMPT_INDEX_COLUMNS = {
    "dataset_id", "source_file_hash", "source_record_sequence", "part_index", "attempt_index",
    "project_id", "product_id", "lot_id", "wafer_id", "coordinate_system_id", "die_x", "die_y",
    "site", "head", "attempt_state", "test_start_time", "test_end_time", "hbin", "sbin",
    "program_key", "ordering_evidence", "attribution_quality",
};

// ---------- Arrow schema helpers ----------

std::shared_ptr<arrow::Field> utf8_field(const std::string& name) {
  return arrow::field(name, arrow::utf8());
}

std::shared_ptr<arrow::Field> nullable_int32_field(const std::string& name) {
  return arrow::field(name, arrow::int32());
}

std::shared_ptr<arrow::Field> nullable_double_field(const std::string& name) {
  return arrow::field(name, arrow::float64());
}

std::shared_ptr<arrow::Field> nullable_bool_field(const std::string& name) {
  return arrow::field(name, arrow::boolean());
}

std::shared_ptr<arrow::Schema> make_long_schema() {
  arrow::FieldVector fields;
  for (const auto& col : LONG_COLUMNS) {
    if (col == "part_index" || col == "attempt_index" || col == "test_num" ||
        col == "test_flag" || col == "param_flag" || col == "record_sequence" ||
        col == "result_index") {
      fields.push_back(nullable_int32_field(col));
    } else if (col == "is_fail") {
      fields.push_back(nullable_bool_field(col));
    } else if (col == "test_item_key") {
      fields.push_back(arrow::field(col, arrow::fixed_size_binary(16), false));
    } else if (col == "record_type") {
      fields.push_back(arrow::field(col, arrow::utf8(), false));
    } else {
      fields.push_back(utf8_field(col));  // result → string
    }
  }
  return arrow::schema(fields);
}

std::shared_ptr<arrow::Schema> make_catalog_schema() {
  arrow::FieldVector fields;
  for (const auto& col : CATALOG_COLUMNS) {
    if (col == "test_num") {
      fields.push_back(nullable_int32_field(col));
    } else if (col == "low_limit" || col == "high_limit") {
      fields.push_back(nullable_double_field(col));
    } else {
      fields.push_back(utf8_field(col));
    }
  }
  return arrow::schema(fields);
}

std::shared_ptr<arrow::Schema> make_part_summary_schema() {
  arrow::FieldVector fields;
  for (const auto& col : PART_SUMMARY_COLUMNS) {
    if (col == "part_index" || col == "attempt_index" || col == "die_x" ||
        col == "die_y" || col == "site_num" || col == "hbin" ||
        col == "sbin" || col == "record_sequence") {
      fields.push_back(nullable_int32_field(col));
    } else if (col == "is_final") {
      fields.push_back(nullable_bool_field(col));
    } else {
      fields.push_back(utf8_field(col));
    }
  }
  return arrow::schema(fields);
}

// ============================================================================
// Crash diagnostics (Task 14 regression hardening)
// ============================================================================

#ifdef _WIN32
static std::ofstream g_crash_log;
static LONG WINAPI unhandled_exception_filter(EXCEPTION_POINTERS* info) {
  if (g_crash_log.is_open()) {
    g_crash_log << "NATIVE_CRASH code=0x" << std::hex << info->ExceptionRecord->ExceptionCode
                << " addr=0x" << info->ExceptionRecord->ExceptionAddress << std::dec << "\n";
    g_crash_log.flush();
  }
  return EXCEPTION_EXECUTE_HANDLER;
}
#endif

// Trace functions now routed through logger.cpp buffered trace.
// Use deepyield_stdf::trace::write_trace() which is buffered (flush every 4096 lines).
// trace_open / trace_close are called from write_wide_parquet.
// trace is OFF by default; enabled via --debug-trace or DEEPYIELD_TRACE=1.

// ---------- Phase 5: Typed appender (replaces per-cell dynamic_cast) ----------

enum class ColumnKind {
    Null,
    String,
    Int32,
    Double,
    Bool,
    Binary
};

struct ColumnAppender {
    ColumnKind kind = ColumnKind::Null;
    arrow::ArrayBuilder* builder = nullptr;
};

/// Infer ColumnKind from an Arrow field type (called once per schema column).
static ColumnKind infer_column_kind(const std::shared_ptr<arrow::DataType>& type) {
    if (!type) return ColumnKind::Null;
    switch (type->id()) {
        case arrow::Type::STRING:
        case arrow::Type::LARGE_STRING:
            return ColumnKind::String;
        case arrow::Type::INT32:
            return ColumnKind::Int32;
        case arrow::Type::DOUBLE:
            return ColumnKind::Double;
        case arrow::Type::BOOL:
            return ColumnKind::Bool;
        case arrow::Type::FIXED_SIZE_BINARY:
            return ColumnKind::Binary;
        default:
            return ColumnKind::Null;
    }
}

/// Build a vector of ColumnAppender from a schema and builders vector.
/// Called once after builder creation.
static std::vector<ColumnAppender> make_appenders(
    const std::shared_ptr<arrow::Schema>& schema,
    const std::vector<std::shared_ptr<arrow::ArrayBuilder>>& builders)
{
    std::vector<ColumnAppender> appenders;
    appenders.reserve(builders.size());
    for (std::size_t i = 0; i < builders.size(); ++i) {
        ColumnAppender app;
        app.kind = infer_column_kind(schema->field(static_cast<int>(i))->type());
        app.builder = builders[i].get();
        appenders.push_back(app);
    }
    return appenders;
}

/// Typed append using pre-computed ColumnKind (static_cast, no RTTI).
/// Phase 5: Replaces the old per-cell dynamic_cast path.
static arrow::Status append_cell_typed(ColumnAppender& app, const CellValue& cell,
                                        const std::string& col_name, int row_idx) {
    ++g_perf().append_cell_calls;

    if (cell.is_null()) {
        return app.builder->AppendNull();
    }

#ifdef DEEPYIELD_STRICT_APPEND_VALIDATE
    // Strict mode: validate cell type matches column kind
    auto mismatch = validate_cell_builder_match(cell, app.builder);
    if (!mismatch.empty()) {
        std::string preview;
        switch (cell.type) {
            case CellValue::String: preview = cell.str_val.substr(0, 120); break;
            case CellValue::Int32:  preview = std::to_string(cell.int_val); break;
            case CellValue::Double: preview = std::to_string(cell.dbl_val); break;
            case CellValue::Bool:   preview = cell.bool_val ? "true" : "false"; break;
            case CellValue::Binary: preview = "binary(" + std::to_string(cell.bin_val.size()) + ")"; break;
            default: preview = "<null>"; break;
        }
        std::string err_msg = "[append-type-mismatch] row=" + std::to_string(row_idx)
            + " col_name=" + col_name
            + " builder_type=" + app.builder->type()->ToString()
            + " mismatch=" + mismatch;
        DY_LOG_ERROR(err_msg);
        return arrow::Status::Invalid(err_msg);
    }
#endif

    switch (app.kind) {
        case ColumnKind::String: {
            auto* b = static_cast<arrow::StringBuilder*>(app.builder);
            if (cell.type == CellValue::Null) return b->AppendNull();
            return b->Append(cell.str_val);
        }
        case ColumnKind::Int32: {
            auto* b = static_cast<arrow::Int32Builder*>(app.builder);
            if (cell.type == CellValue::Null) return b->AppendNull();
            return b->Append(cell.int_val);
        }
        case ColumnKind::Double: {
            auto* b = static_cast<arrow::DoubleBuilder*>(app.builder);
            if (cell.type == CellValue::Null) return b->AppendNull();
            return b->Append(cell.dbl_val);
        }
        case ColumnKind::Bool: {
            auto* b = static_cast<arrow::BooleanBuilder*>(app.builder);
            if (cell.type == CellValue::Null) return b->AppendNull();
            return b->Append(cell.bool_val);
        }
        case ColumnKind::Binary: {
            auto* b = static_cast<arrow::FixedSizeBinaryBuilder*>(app.builder);
            if (cell.type == CellValue::Null) return b->AppendNull();
            if (cell.bin_val.size() != 16) return b->AppendNull();
            return b->Append(cell.bin_val.data());
        }
        default:
            return arrow::Status::Invalid("Unsupported column kind for ", col_name);
    }
}

// ---------- builder appenders (with diagnostics) ----------

/// Validate that cell type matches builder type.  Returns "" if ok,
/// or an error description if mismatched.
static std::string validate_cell_builder_match(const CellValue& cell,
                                                arrow::ArrayBuilder* builder) {
  if (cell.is_null()) return "";
  auto bt = builder->type();
  switch (cell.type) {
    case CellValue::String:
      if (bt->id() != arrow::Type::STRING && bt->id() != arrow::Type::LARGE_STRING)
        return "expected STRING builder, got type " + bt->ToString();
      break;
    case CellValue::Int32:
      if (bt->id() != arrow::Type::INT32)
        return "expected INT32 builder, got type " + bt->ToString();
      break;
    case CellValue::Double:
      if (bt->id() != arrow::Type::DOUBLE)
        return "expected DOUBLE builder, got type " + bt->ToString();
      break;
    case CellValue::Bool:
      if (bt->id() != arrow::Type::BOOL)
        return "expected BOOL builder, got type " + bt->ToString();
      break;
    case CellValue::Binary:
      if (bt->id() != arrow::Type::FIXED_SIZE_BINARY)
        return "expected FIXED_SIZE_BINARY builder, got type " + bt->ToString();
      break;
    default:
      return "unknown CellValue type " + std::to_string(static_cast<int>(cell.type));
  }
  return "";
}

arrow::Status append_cell(arrow::ArrayBuilder* builder, const CellValue& cell,
                          const std::string& col_name, int row_idx) {
  using arrow::Int32Builder;
  using arrow::DoubleBuilder;
  using arrow::StringBuilder;
  using arrow::BooleanBuilder;

  ++g_perf().append_cell_calls;

  if (cell.is_null()) {
    return builder->AppendNull();
  }

  // Validate type match; mismatched types → fatal error to prevent memory corruption
  auto mismatch = validate_cell_builder_match(cell, builder);
  if (!mismatch.empty()) {
    // Build value preview (first 120 chars)
    std::string preview;
    switch (cell.type) {
      case CellValue::String: preview = cell.str_val.substr(0, 120); break;
      case CellValue::Int32:  preview = std::to_string(cell.int_val); break;
      case CellValue::Double: preview = std::to_string(cell.dbl_val); break;
      case CellValue::Bool:   preview = cell.bool_val ? "true" : "false"; break;
      case CellValue::Binary: preview = "binary(" + std::to_string(cell.bin_val.size()) + ")"; break;
      default: preview = "<null>"; break;
    }
    std::string cell_type_str;
    switch (cell.type) {
      case CellValue::Null:   cell_type_str = "Null"; break;
      case CellValue::String: cell_type_str = "String"; break;
      case CellValue::Int32:  cell_type_str = "Int32"; break;
      case CellValue::Double: cell_type_str = "Double"; break;
      case CellValue::Bool:   cell_type_str = "Bool"; break;
      case CellValue::Binary: cell_type_str = "Binary"; break;
      default: cell_type_str = "Unknown(" + std::to_string(static_cast<int>(cell.type)) + ")";
    }
    std::string err_msg = "[append-type-mismatch] row=" + std::to_string(row_idx)
        + " col_name=" + col_name
        + " cell_type=" + cell_type_str
        + " builder_type=" + builder->type()->ToString()
        + " expected=" + builder->type()->ToString()
        + " mismatch=" + mismatch
        + " cell_value_preview=" + preview;
    std::cerr << err_msg << "\n";
    std::cerr.flush();
    return arrow::Status::Invalid(err_msg);
  }

  switch (cell.type) {
    case CellValue::String: {
      ++g_perf().dynamic_cast_calls;
      auto* b = dynamic_cast<StringBuilder*>(builder);
      if (!b) return arrow::Status::Invalid("dynamic_cast<StringBuilder> failed for ", col_name);
      return b->Append(cell.str_val);
    }
    case CellValue::Int32: {
      ++g_perf().dynamic_cast_calls;
      auto* b = dynamic_cast<Int32Builder*>(builder);
      if (!b) return arrow::Status::Invalid("dynamic_cast<Int32Builder> failed for ", col_name);
      return b->Append(cell.int_val);
    }
    case CellValue::Double: {
      ++g_perf().dynamic_cast_calls;
      auto* b = dynamic_cast<DoubleBuilder*>(builder);
      if (!b) return arrow::Status::Invalid("dynamic_cast<DoubleBuilder> failed for ", col_name);
      return b->Append(cell.dbl_val);
    }
    case CellValue::Bool: {
      ++g_perf().dynamic_cast_calls;
      auto* b = dynamic_cast<BooleanBuilder*>(builder);
      if (!b) return arrow::Status::Invalid("dynamic_cast<BooleanBuilder> failed for ", col_name);
      return b->Append(cell.bool_val);
    }
    case CellValue::Binary: {
      ++g_perf().dynamic_cast_calls;
      auto* b = dynamic_cast<arrow::FixedSizeBinaryBuilder*>(builder);
      if (!b) return arrow::Status::Invalid("dynamic_cast<FixedSizeBinaryBuilder> failed for ", col_name);
      if (cell.bin_val.size() != 16) return b->AppendNull();
      return b->Append(cell.bin_val.data());
    }
    default:
      return builder->AppendNull();
  }
}

// Write a Row using a column→index map and typed appenders (Phase 5: no dynamic_cast).
arrow::Status append_row(const Row& row,
                         const std::unordered_map<std::string, int>& col_index,
                         std::vector<std::shared_ptr<arrow::ArrayBuilder>>& builders,
                         const std::shared_ptr<arrow::Schema>& schema,
                         std::vector<ColumnAppender>& appenders,
                         int global_row_idx) {
  std::size_t ncols = builders.size();

  // Ensure appenders are built (lazily, once per batch)
  if (appenders.empty() && !builders.empty()) {
    appenders = make_appenders(schema, builders);
  }

  std::vector<const CellValue*> ordered(ncols, nullptr);

  for (const auto& [key, value] : row) {
    const auto it = col_index.find(key);
    if (it != col_index.end() && it->second >= 0 && static_cast<std::size_t>(it->second) < ncols) {
      ordered[static_cast<std::size_t>(it->second)] = &value;
    }
  }

  for (std::size_t i = 0; i < ncols; ++i) {
    if (!builders[i]) {
      return arrow::Status::Invalid("append_row row=", global_row_idx,
                                    " col=", i, " builder is null");
    }

    if (ordered[i]) {
      std::string col_name = (i < static_cast<std::size_t>(schema->num_fields()))
          ? schema->field(static_cast<int>(i))->name() : ("col_" + std::to_string(i));
      // Phase 5: use typed appender (static_cast, no dynamic_cast)
      auto st = append_cell_typed(appenders[i], *ordered[i], col_name, global_row_idx);
      if (!st.ok()) return st;
    } else {
      auto st = builders[i]->AppendNull();
      if (!st.ok()) {
        return arrow::Status::Invalid("append_row row=", global_row_idx,
                                      " col=", i, " AppendNull failed: ", st.ToString());
      }
    }
  }
  return arrow::Status::OK();
}

// ColumnId-keyed variant of append_row.  Resolves each cell's output column via
// id_to_index[col_id] (array index) instead of a string-keyed map lookup.
arrow::Status append_row_sparse(const SparseRow& row,
                                const std::vector<int>& id_to_index,
                                std::vector<std::shared_ptr<arrow::ArrayBuilder>>& builders,
                                const std::shared_ptr<arrow::Schema>& schema,
                                std::vector<ColumnAppender>& appenders,
                                int global_row_idx) {
  std::size_t ncols = builders.size();

  if (appenders.empty() && !builders.empty()) {
    appenders = make_appenders(schema, builders);
  }

  std::vector<const CellValue*> ordered(ncols, nullptr);

  for (const auto& [col_id, value] : row) {
    if (col_id < id_to_index.size()) {
      const int idx = id_to_index[col_id];
      if (idx >= 0 && static_cast<std::size_t>(idx) < ncols) {
        ordered[static_cast<std::size_t>(idx)] = &value;
      }
    }
  }

  for (std::size_t i = 0; i < ncols; ++i) {
    if (!builders[i]) {
      return arrow::Status::Invalid("append_row_sparse row=", global_row_idx,
                                    " col=", i, " builder is null");
    }
    if (ordered[i]) {
      std::string col_name = (i < static_cast<std::size_t>(schema->num_fields()))
          ? schema->field(static_cast<int>(i))->name() : ("col_" + std::to_string(i));
      auto st = append_cell_typed(appenders[i], *ordered[i], col_name, global_row_idx);
      if (!st.ok()) return st;
    } else {
      auto st = builders[i]->AppendNull();
      if (!st.ok()) {
        return arrow::Status::Invalid("append_row_sparse row=", global_row_idx,
                                      " col=", i, " AppendNull failed: ", st.ToString());
      }
    }
  }
  return arrow::Status::OK();
}

arrow::Status finish_batch(
    std::shared_ptr<arrow::Schema> schema,
    std::vector<std::shared_ptr<arrow::ArrayBuilder>>& builders,
    std::shared_ptr<arrow::RecordBatch>* out) {
  DY_LOG_DEBUG("[stage] finish_arrays_start builder_count=" + std::to_string(builders.size()));

  arrow::ArrayVector arrays;
  arrays.reserve(builders.size());
  for (auto& b : builders) {
    std::shared_ptr<arrow::Array> arr;
    ARROW_RETURN_NOT_OK(b->Finish(&arr));
    arrays.push_back(std::move(arr));
  }
  DY_LOG_DEBUG("[stage] finish_arrays_done array_count=" + std::to_string(arrays.size()));

  // Debug stop: after finish arrays
  if (g_debug.stop_after_finish_arrays) {
    DY_LOG_INFO("[stage] debug-stop-after-finish-arrays done, exiting");
    std::cerr.flush();
    std::exit(0);
  }

  // Array length consistency check (exit code 42 if mismatch)
  std::int64_t expected_rows = arrays.empty() ? 0 : arrays[0]->length();
  for (std::size_t col = 0; col < arrays.size(); ++col) {
    if (!arrays[col]) {
      std::cerr << "[array-length-mismatch] col=" << col
                << " array is null" << std::endl;
      std::cerr.flush();
      return arrow::Status::Invalid(
          "[array-length-mismatch] col=", col, " array is null");
    }
    std::int64_t actual = arrays[col]->length();
    if (actual != expected_rows) {
      std::string col_name = (col < static_cast<std::size_t>(schema->num_fields()))
          ? schema->field(static_cast<int>(col))->name()
          : ("col_" + std::to_string(col));
      std::cerr << "[array-length-mismatch] col=" << col
                << " col_name=" << col_name
                << " expected=" << expected_rows
                << " actual=" << actual << std::endl;
      std::cerr.flush();
      return arrow::Status::Invalid(
          "[array-length-mismatch] col=", col, " col_name=", col_name,
          " expected=", expected_rows, " actual=", actual);
    }
  }

  std::cerr << "[stage] recordbatch_start rows=" << expected_rows
            << " columns=" << arrays.size() << std::endl;
  std::cerr.flush();
  const auto row_count = static_cast<std::int64_t>(expected_rows);
  *out = arrow::RecordBatch::Make(std::move(schema), row_count, std::move(arrays));
  std::cerr << "[stage] recordbatch_done rows=" << row_count
            << " columns=" << arrays.size() << std::endl;
  std::cerr.flush();

  // Debug stop: after recordbatch
  if (g_debug.stop_after_recordbatch) {
    DY_LOG_INFO("[stage] debug-stop-after-recordbatch done, exiting");
    std::cerr.flush();
    std::exit(0);
  }

  return arrow::Status::OK();
}

arrow::Status reset_builders(std::shared_ptr<arrow::Schema> schema,
                             std::vector<std::shared_ptr<arrow::ArrayBuilder>>& builders) {
  builders.clear();
  builders.reserve(schema->num_fields());
  for (int i = 0; i < schema->num_fields(); ++i) {
    std::unique_ptr<arrow::ArrayBuilder> b;
    ARROW_RETURN_NOT_OK(
        arrow::MakeBuilder(arrow::default_memory_pool(), schema->field(i)->type(), &b));
    builders.push_back(std::move(b));
  }
  return arrow::Status::OK();
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

std::string json_escape(const std::string& value) {
  std::ostringstream out;
  for (unsigned char ch : value) {
    switch (ch) {
      case '"':  out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\b': out << "\\b";  break;
      case '\f': out << "\\f";  break;
      case '\n': out << "\\n";  break;
      case '\r': out << "\\r";  break;
      case '\t': out << "\\t";  break;
      default:
        if (ch < 0x20 || ch >= 0x80)
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(ch);
        else
          out << ch;
    }
  }
  return out.str();
}

void write_json_string(std::ostream& out, const std::string& text) {
  out << '"' << json_escape(text) << '"';
}

// ---------- wide-schema helpers ----------

bool starts_with(const std::string& s, const std::string& prefix) {
  return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

bool is_wide_identity(const std::string& name) {
  for (const auto& c : WIDE_IDENTITY_COLUMNS)
    if (c == name) return true;
  return false;
}

bool is_wide_meta(const std::string& name) {
  for (const auto& c : WIDE_META_COLUMNS)
    if (c == name) return true;
  // SDR_* columns are per-site metadata, not test results
  if (starts_with(name, "SDR_")) return true;
  return false;
}

// Legacy fallback column: TEST_<digits>, e.g. TEST_10, TEST_123, TEST_7001.
// These are generated when test_text is empty or cleans to UNKNOWN, and
// safe_column_name() cannot produce a normal "<num>_<text>" name.
static bool is_legacy_test_fallback_column(const std::string& col) {
  constexpr std::string_view prefix = "TEST_";
  if (col.size() <= prefix.size()) return false;
  if (col.rfind(prefix, 0) != 0) return false;  // does not start with "TEST_"
  for (size_t i = prefix.size(); i < col.size(); ++i) {
    if (!std::isdigit(static_cast<unsigned char>(col[i]))) return false;
  }
  return true;
}

// Wide test value column: either digit-prefixed (e.g. 7001_P7001_FAB_WAFER)
// or legacy TEST_<digits> fallback (e.g. TEST_10).
static bool is_wide_test_value_column(const std::string& col) {
  if (!col.empty() && std::isdigit(static_cast<unsigned char>(col[0]))) return true;
  return is_legacy_test_fallback_column(col);
}

std::shared_ptr<arrow::Schema> make_wide_schema(const std::vector<std::string>& columns,
                                                  const std::string& label = "") {
  arrow::FieldVector fields;
  int n = static_cast<int>(columns.size());

  // Apply schema_prefix debug flag
  if (g_debug.schema_prefix >= 0) {
    if (g_debug.schema_prefix < n) {
      std::cerr << "[stdf2parquet] schema_prefix=" << g_debug.schema_prefix
                << " limiting columns from " << n << " to " << g_debug.schema_prefix << "\n";
      std::cerr.flush();
      n = g_debug.schema_prefix;
    } else {
      std::cerr << "[stdf2parquet] WARNING schema_prefix=" << g_debug.schema_prefix
                << " > columns.size()=" << n << ", using all columns\n";
      std::cerr.flush();
    }
  }

  std::cerr << "[stage] build_fields_start field_count=" << n;
  if (!label.empty()) std::cerr << " file=" << label;
  std::cerr << "\n";
  std::cerr.flush();

  std::cerr << "[stdf2parquet] schema build begin cols=" << n;
  if (!label.empty()) std::cerr << " file=" << label;
  std::cerr << "\n";
  std::cerr.flush();
  for (int i = 0; i < n; ++i) {
    const auto& col = columns[i];
    // Progress log every 500 columns
    if (i > 0 && (i % 500 == 0)) {
      DY_LOG_DEBUG("[stdf2parquet] schema build col=" + std::to_string(i) + "/" + std::to_string(n)
                + " type=" + (starts_with(col, "__") ? "meta" : (is_wide_identity(col) ? "id" : "test"))
                + " name=" + col);
    }
    // ---------- column type classification ----------
    // Explicit, fail-fast logic.  Every column must match a known category.
    // Unknown columns that do not look like test results (digit-prefixed)
    // cause an immediate error so the schema definition can be updated.
    // Identity columns that are int32 (wide + part_summary, both cases)
    if (col == "total_prr" || col == "valid_prr" || col == "pass_prr" ||
        col == "fail_prr" || col == "abort_prr" || col == "unknown_prr" ||
        col == "DIE_X" || col == "DIE_Y" || col == "die_x" || col == "die_y" ||
        col == "HBIN" || col == "SBIN" || col == "hbin" || col == "sbin" ||
        col == "SITE" || col == "site_num" || col == "__part_index" || col == "__retest_index" ||
        col == "part_index" || col == "attempt_index" || col == "record_sequence" ||
        col == "source_record_sequence") {
      fields.push_back(nullable_int32_field(col));
    } else if (col == "__is_retest" || col == "__is_final_test" || col == "is_final") {
      fields.push_back(nullable_bool_field(col));
    } else if (col == "__part_flag" || col == "__test_time_ms") {
      fields.push_back(nullable_int32_field(col));
    } else if (starts_with(col, "__")) {
      fields.push_back(utf8_field(col));
    } else if (col == "schema_version" || col == "source_asset_id" || col == "source_sha256" ||
               col == "source_file" || col == "segment_id" || col == "project_id" ||
               col == "product_id" || col == "lot_id" || col == "program_name" ||
               col == "program_revision" || col == "start_time_utc" ||
               col == "finish_time_utc" || col == "source_timezone" ||
               col == "hbin_counts_json" || col == "sbin_counts_json" ||
               col == "result_source" || col == "parser_version" ||
               col == "result_policy_id" || col == "result_policy_status" ||
               col == "reason_codes_json" || col == "dataset_id" || col == "source_file_hash" ||
               col == "coordinate_system_id" || col == "attempt_state" || col == "test_start_time" ||
               col == "test_end_time" || col == "program_key" || col == "ordering_evidence" ||
               col == "attribution_quality" || is_wide_identity(col) || col == "lot_id" || col == "wafer_id" ||
               col == "die_x" || col == "die_y" || col == "site_num" || col == "site" || col == "head") {
      // Covers Name, LOT_ID, WAFER_ID, Lot_ID, device, part_type,
      // start_time, finish_time, test_program_name, and part_summary cols
      fields.push_back(utf8_field(col));
    } else if (starts_with(col, "SDR_")) {
      // SDR per-site metadata — int32 for head/site numbers, utf8 for all others
      if (col == "SDR_HEAD_NUM" || col == "SDR_SITE_NUM") {
        fields.push_back(nullable_int32_field(col));
      } else {
        fields.push_back(utf8_field(col));
      }
    } else if (is_wide_test_value_column(col)) {
      // Dynamic test column — digit-prefixed (e.g. 7001_P7001_FAB_WAFER)
      // or legacy TEST_<digits> fallback (e.g. TEST_10) → double
      fields.push_back(nullable_double_field(col));
    } else {
      // Unknown column that is NOT a recognised test result column
      // (digit-prefixed or legacy TEST_<digits> fallback).
      // This is a schema definition bug — the column must be added to
      // WIDE_IDENTITY_COLUMNS, WIDE_META_COLUMNS, or handled here explicitly.
      std::cerr << "[schema-check] FATAL: unknown column type col=" << col
                << " (not identity, meta, SDR_*, digit-prefixed, or TEST_<digits>)\n";
      std::cerr.flush();
      throw std::runtime_error(
          "[schema-check] Unknown column type: \"" + col +
          "\". Add to WIDE_IDENTITY_COLUMNS or WIDE_META_COLUMNS, "
          "or add explicit handling in make_wide_schema().");
    }
  }
  std::cerr << "[stage] build_fields_done field_count=" << n << "\n";
  std::cerr.flush();

  std::cerr << "[stage] validate_fields_start\n";
  std::cerr.flush();
  if (!validate_fields_before_schema(fields)) {
    std::cerr << "[schema-check] FATAL: validate_fields_before_schema failed, aborting\n";
    std::cerr.flush();
    throw std::runtime_error("[schema-check] validate_fields_before_schema found fatal issues");
  }
  std::cerr << "[stage] validate_fields_done\n";
  std::cerr.flush();

  std::cerr << "[stage] arrow_schema_start field_count=" << fields.size() << "\n";
  std::cerr.flush();
  std::cerr << "[stdf2parquet] schema build all=" << n << " fields built, calling arrow::schema...\n";
  std::cerr.flush();
  auto result = arrow::schema(fields);
  std::cerr << "[stage] arrow_schema_done num_fields=" << result->num_fields() << "\n";
  std::cerr.flush();

  std::cerr << "[stage] schema_validate_start\n";
  std::cerr.flush();
  if (result && result->num_fields() > 0) {
    bool distinct = result->HasDistinctFieldNames();
    std::cerr << "[stage] schema_validate_done distinct_names="
              << (distinct ? "true" : "false") << "\n";
    std::cerr.flush();
    if (!distinct) {
      std::cerr << "[stage] WARNING: schema has duplicate field names\n";
      std::cerr.flush();
    }
  } else {
    std::cerr << "[stage] schema_validate_FAILED: schema is null or empty\n";
    std::cerr.flush();
  }

  std::cerr << "[stdf2parquet] schema build done fields=" << result->num_fields() << "\n";
  std::cerr.flush();
  return result;
}

// Collect and order wide columns from rows
// Assemble the final wide column order from the sets of present leading
// (identity/meta) and dynamic (test) column names.  Deterministic and
// independent of row iteration order: leading in canonical order, missing
// identity columns inserted before the first non-identity, dynamic columns
// sorted alphabetically.
std::vector<std::string> assemble_wide_columns(const std::set<std::string>& present_leading,
                                               const std::set<std::string>& dynamic) {
  std::vector<std::string> leading;
  for (const auto& col : WIDE_IDENTITY_COLUMNS) {
    if (present_leading.count(col)) leading.push_back(col);
  }
  for (const auto& col : WIDE_META_COLUMNS) {
    if (present_leading.count(col)) leading.push_back(col);
  }
  // Required identity columns
  for (const auto& required : WIDE_IDENTITY_COLUMNS) {
    bool found = false;
    for (const auto& col : leading) {
      if (col == required) { found = true; break; }
    }
    if (!found) {
      int pos = 0;
      for (const auto& col : leading) {
        if (!is_wide_identity(col)) break;
        ++pos;
      }
      leading.insert(leading.begin() + pos, required);
    }
  }
  // Sorted dynamic columns
  std::vector<std::string> sorted_dynamic(dynamic.begin(), dynamic.end());
  std::sort(sorted_dynamic.begin(), sorted_dynamic.end());
  std::vector<std::string> result;
  for (const auto& col : leading) result.push_back(col);
  for (const auto& col : sorted_dynamic) result.push_back(col);
  return result;
}

std::vector<std::string> ordered_wide_columns(const std::vector<Row>& rows) {
  std::set<std::string> seen;
  for (const auto& meta : WIDE_META_COLUMNS) seen.insert(meta);
  for (const auto& id : WIDE_IDENTITY_COLUMNS) seen.insert(id);
  std::set<std::string> dynamic;
  std::set<std::string> present_leading;
  for (const auto& row : rows) {
    for (const auto& [key, _] : row) {
      if (seen.find(key) == seen.end()) dynamic.insert(key);
      if (is_wide_identity(key) || is_wide_meta(key)) present_leading.insert(key);
    }
  }
  return assemble_wide_columns(present_leading, dynamic);
}

// ColumnId-keyed variant (fast path): resolves names via id_to_name and
// classifies identity/meta vs dynamic exactly as ordered_wide_columns does.
// NB: the dynamic set is keyed off the *canonical list* (WIDE_*_COLUMNS), NOT
// the is_wide_meta() pattern — a column like SDR_HEAD_NUM matches the SDR_
// pattern but is NOT in the canonical lists, so it lands in dynamic only.
std::vector<std::string> ordered_wide_columns_sparse(const std::vector<SparseRow>& rows,
                                                     const std::vector<std::string>& id_to_name) {
  std::set<std::string> seen;
  for (const auto& meta : WIDE_META_COLUMNS) seen.insert(meta);
  for (const auto& id : WIDE_IDENTITY_COLUMNS) seen.insert(id);
  std::set<std::string> dynamic;
  std::set<std::string> present_leading;
  for (const auto& row : rows) {
    for (const auto& [col_id, _] : row) {
      if (col_id >= id_to_name.size()) continue;
      const std::string& name = id_to_name[col_id];
      if (seen.find(name) == seen.end()) dynamic.insert(name);
      if (is_wide_identity(name) || is_wide_meta(name)) present_leading.insert(name);
    }
  }
  return assemble_wide_columns(present_leading, dynamic);
}

// ---------- debug / validation helpers ----------

bool validate_fields_before_schema(const arrow::FieldVector& fields) {
  std::cerr << "[schema-check] fields.size=" << fields.size() << std::endl;

  bool fatal = false;
  std::set<std::string> seen_names;
  int n = static_cast<int>(fields.size());

  for (int i = 0; i < n; ++i) {
    const auto& f = fields[i];
    bool print_detail = (i < 20) || (i % 500 == 0) || (i >= n - 10);

    // 1. null field
    if (!f) {
      std::cerr << "[schema-check] FATAL i=" << i << " field is null\n";
      fatal = true;
      continue;
    }

    // 2. null type
    auto type = f->type();
    if (!type) {
      std::cerr << "[schema-check] FATAL i=" << i << " name=" << f->name()
                << " type is null" << std::endl;
      fatal = true;
      if (!print_detail) continue;
    }

    // 3. empty name
    if (f->name().empty()) {
      std::cerr << "[schema-check] FATAL i=" << i << " name is empty\n";
      fatal = true;
      if (!print_detail) continue;
    }

    // 4. duplicate name
    if (seen_names.count(f->name())) {
      std::cerr << "[schema-check] WARNING i=" << i << " duplicate name="
                << f->name() << std::endl;
    }
    seen_names.insert(f->name());

    if (print_detail) {
      std::cerr << "[schema-check] i=" << i
                << " field_ptr=" << f.get()
                << " type_ptr=" << (type ? type.get() : nullptr)
                << " name=" << f->name()
                << " type=" << (type ? type->ToString() : "NULL") << std::endl;
    }
  }

  std::cerr << "[schema-check] done fatal=" << (fatal ? "true" : "false") << std::endl;
  return !fatal;
}

}  // namespace

// ---------- public debug / diagnostic helpers ----------

int debug_arrow_schema_test(int num_fields) {
  std::cerr << "[debug-arrow-schema] building fields n=" << num_fields << std::endl;
  arrow::FieldVector fields;
  fields.reserve(num_fields);
  for (int i = 0; i < num_fields; ++i) {
    fields.push_back(arrow::field("col_" + std::to_string(i), arrow::float64()));
  }
  std::cerr << "[debug-arrow-schema] fields built: " << fields.size() << std::endl;

  DY_LOG_INFO("[debug-arrow-schema] calling arrow::schema...");
  auto schema = arrow::schema(fields);
  std::cerr << "[debug-arrow-schema] schema created num_fields="
            << schema->num_fields() << std::endl;

  DY_LOG_INFO("[debug-arrow-schema] checking HasDistinctFieldNames...");
  bool distinct = schema->HasDistinctFieldNames();
  std::cerr << "[debug-arrow-schema] schema validate "
            << (distinct ? "OK (distinct names)" : "WARNING (duplicate names)")
            << " num_fields=" << schema->num_fields() << std::endl;
  return 0;
}

void print_env_diagnostics(int argc, char** argv) {
  DY_LOG_INFO("[env] stdf2parquet starting");

  // Build type
#ifdef _DEBUG
  DY_LOG_INFO("[env] build_type=Debug");
#elif defined(NDEBUG)
  DY_LOG_INFO("[env] build_type=Release");
#else
  DY_LOG_INFO("[env] build_type=Unknown");
#endif

  // MSVC runtime info
#if defined(_MSC_VER)
  std::cerr << "[env] compiler=MSVC"
            << " _MSC_VER=" << _MSC_VER
            << " _MSVC_LANG=" << _MSVC_LANG << std::endl;
#if defined(_DLL)
  DY_LOG_INFO("[env] msvc_runtime=/MD (dynamic link)");
#elif defined(_MT)
  DY_LOG_INFO("[env] msvc_runtime=/MT (static link)");
#else
  DY_LOG_INFO("[env] msvc_runtime=unknown");
#endif
#else
  DY_LOG_INFO("[env] compiler=non-MSVC");
#endif

  // Arrow version
#ifdef ARROW_VERSION_MAJOR
  std::cerr << "[env] arrow_version=" << ARROW_VERSION_MAJOR << "."
            << ARROW_VERSION_MINOR << "." << ARROW_VERSION_PATCH << std::endl;
#else
  DY_LOG_INFO("[env] arrow_version=unknown");
#endif

  // CWD
  std::error_code ec;
  std::cerr << "[env] cwd=" << fs::current_path(ec).string() << std::endl;

  // argv
  std::cerr << "[env] argv=";
  for (int i = 0; i < argc; ++i) {
    if (i > 0) std::cerr << " ";
    std::cerr << argv[i];
  }
  std::cerr << std::endl;
}

// ---------- Impl ----------

struct ParquetAssetWriter::Impl {
  fs::path output_dir;
  fs::path parquet_dir;
  std::int64_t chunk_size;
  bool write_long = true;
  bool write_wide_all = true;

  // Long writer
  std::shared_ptr<arrow::Schema> long_schema;
  std::shared_ptr<arrow::io::OutputStream> long_out;
  std::unique_ptr<parquet::arrow::FileWriter> long_writer;
  std::vector<std::shared_ptr<arrow::ArrayBuilder>> long_builders;
  std::vector<ColumnAppender> long_appenders;  // Phase 5: typed appenders
  std::unordered_map<std::string, int> long_col_index;
  std::int64_t long_buffered = 0;

  // Long-table fast path: typed builder cache (Phase 2 of long-table optimization)
  struct LongBuilderCache {
      arrow::Int32Builder* part_index = nullptr;
      arrow::Int32Builder* attempt_index = nullptr;
      arrow::Int32Builder* test_num = nullptr;
      arrow::StringBuilder* result = nullptr;
      arrow::BooleanBuilder* is_fail = nullptr;
      arrow::Int32Builder* test_flag = nullptr;
      arrow::Int32Builder* param_flag = nullptr;
      arrow::Int32Builder* record_sequence = nullptr;
      arrow::StringBuilder* record_type = nullptr;
      arrow::Int32Builder* result_index = nullptr;
      arrow::FixedSizeBinaryBuilder* test_item_key = nullptr;
  };
  LongBuilderCache long_fast_builders_;
  bool long_fast_initialized_ = false;
  bool use_long_fast_ = true;  // --long-writer fast (default) or legacy

  // Cache typed builder pointers from long_builders vector after init/reset.
  void init_long_fast_builders() {
      if (long_builders.size() < 11) return;
      long_fast_builders_.part_index      = static_cast<arrow::Int32Builder*>(long_builders[0].get());
      long_fast_builders_.attempt_index   = static_cast<arrow::Int32Builder*>(long_builders[1].get());
      long_fast_builders_.test_num        = static_cast<arrow::Int32Builder*>(long_builders[2].get());
      long_fast_builders_.result          = static_cast<arrow::StringBuilder*>(long_builders[3].get());
      long_fast_builders_.is_fail         = static_cast<arrow::BooleanBuilder*>(long_builders[4].get());
      long_fast_builders_.test_flag       = static_cast<arrow::Int32Builder*>(long_builders[5].get());
      long_fast_builders_.param_flag      = static_cast<arrow::Int32Builder*>(long_builders[6].get());
      long_fast_builders_.record_sequence  = static_cast<arrow::Int32Builder*>(long_builders[7].get());
      long_fast_builders_.record_type      = static_cast<arrow::StringBuilder*>(long_builders[8].get());
      long_fast_builders_.result_index     = static_cast<arrow::Int32Builder*>(long_builders[9].get());
      long_fast_builders_.test_item_key    = static_cast<arrow::FixedSizeBinaryBuilder*>(long_builders[10].get());
      long_fast_initialized_ = true;
  }

  // Validate long schema matches the fixed fast-path layout.
  arrow::Status validate_long_fast_schema() {
      if (!long_schema) return arrow::Status::Invalid("long_schema is null");
      if (long_schema->num_fields() != 11) {
          return arrow::Status::Invalid("long fast schema expects 11 fields, got ",
                                        long_schema->num_fields());
      }
      auto check = [&](int i, const std::string& name,
                        std::shared_ptr<arrow::DataType> type) -> arrow::Status {
          const auto& f = long_schema->field(i);
          if (f->name() != name)
              return arrow::Status::Invalid("long schema field[", i, "] name mismatch: expected ",
                                           name, ", got ", f->name());
          if (!f->type()->Equals(type))
              return arrow::Status::Invalid("long schema field[", i, "] type mismatch for ", name,
                                           ": expected ", type->ToString(), ", got ", f->type()->ToString());
          return arrow::Status::OK();
      };
      ARROW_RETURN_NOT_OK(check(0, "part_index", arrow::int32()));
      ARROW_RETURN_NOT_OK(check(1, "attempt_index", arrow::int32()));
      ARROW_RETURN_NOT_OK(check(2, "test_num", arrow::int32()));
      ARROW_RETURN_NOT_OK(check(3, "result", arrow::utf8()));
      ARROW_RETURN_NOT_OK(check(4, "is_fail", arrow::boolean()));
      ARROW_RETURN_NOT_OK(check(5, "test_flag", arrow::int32()));
      ARROW_RETURN_NOT_OK(check(6, "param_flag", arrow::int32()));
      ARROW_RETURN_NOT_OK(check(7, "record_sequence", arrow::int32()));
      ARROW_RETURN_NOT_OK(check(8, "record_type", arrow::utf8()));
      ARROW_RETURN_NOT_OK(check(9, "result_index", arrow::int32()));
      ARROW_RETURN_NOT_OK(check(10, "test_item_key", arrow::fixed_size_binary(16)));
      if (!long_fast_builders_.part_index || !long_fast_builders_.attempt_index ||
          !long_fast_builders_.test_num || !long_fast_builders_.result ||
          !long_fast_builders_.is_fail || !long_fast_builders_.test_flag ||
          !long_fast_builders_.param_flag || !long_fast_builders_.record_sequence ||
          !long_fast_builders_.record_type || !long_fast_builders_.result_index ||
          !long_fast_builders_.test_item_key) {
          return arrow::Status::Invalid("long fast builder cache contains null pointer");
      }
      return arrow::Status::OK();
  }

  // Wide_all buffer (collected in memory, written at close).
  // ColumnId-keyed sparse rows: appended per PRR without building a
  // string-keyed Row; resolved to output columns at close via id_to_name_.
  std::vector<SparseRow> wide_all_sparse_rows_;
  std::vector<std::string> id_to_name_;
  std::unordered_map<std::string, std::uint32_t> name_to_id_;
  std::uint32_t attempt_key_id_ = 0;

  // Catalog buffer
  std::vector<Row> catalog_rows;

  // Part_summary buffer (plan §5.4 — Task 6). Buffered; is_final set at close().
  std::vector<Row> part_summary_rows;

  // Counts
  std::int64_t wide_all_count_ = 0;
  std::int64_t long_count_ = 0;
  std::int64_t catalog_count_ = 0;
  std::int64_t wide_count_ = 0;
  std::int64_t part_summary_count_ = 0;
  std::int64_t ft_execution_fact_count_ = 0;
  std::int64_t ews_attempt_count_ = 0;

  // Manifest status (plan §11 — Task 5)
  std::string manifest_status_ = "succeeded";

  // Per-table write timing (plan §12 — Task 9)
  double write_long_sec_ = 0.0;
  double write_wide_all_sec_ = 0.0;
  double write_part_summary_sec_ = 0.0;

  bool opened = false;
  bool closed = false;

  Impl(const fs::path& out_dir, std::int64_t cs, bool wl, bool wwa)
      : output_dir(out_dir), chunk_size(cs), write_long(wl), write_wide_all(wwa) {
    if (chunk_size <= 0) {
      const char* env = std::getenv("DEEPYIELD_PARQUET_CHUNK_SIZE");
      chunk_size = env ? std::stoll(env) : 100000;
    }
    if (chunk_size < 1000) chunk_size = 1000;
  }

  void open() {
    if (opened) return;
    parquet_dir = output_dir / "parquet";
    fs::create_directories(parquet_dir);

    if (write_long) {
      long_schema = make_long_schema();
      for (int i = 0; i < long_schema->num_fields(); ++i) {
        long_col_index[long_schema->field(i)->name()] = i;
      }
      // Write to .partial, rename atomically at close (plan §8.1 — Task 7)
      auto long_path = (parquet_dir / "long.parquet.partial").string();
      auto out_result = arrow::io::FileOutputStream::Open(long_path);
      if (!out_result.ok())
        throw std::runtime_error("Cannot open long.parquet: " + out_result.status().ToString());
      long_out = *out_result;

      auto writer_result = parquet::arrow::FileWriter::Open(
          *long_schema, arrow::default_memory_pool(), long_out,
          parquet::default_writer_properties(),
          parquet::default_arrow_writer_properties());
      if (!writer_result.ok())
        throw std::runtime_error("Cannot create long Parquet writer: " + writer_result.status().ToString());
      long_writer = std::move(*writer_result);

      auto reset_status = reset_builders(long_schema, long_builders);
      if (!reset_status.ok())
        throw std::runtime_error("Cannot init long builders: " + reset_status.ToString());
    }

    opened = true;
  }

  void flush_long() {
    if (long_buffered == 0) return;
    std::shared_ptr<arrow::RecordBatch> batch;
    auto status = finish_batch(long_schema, long_builders, &batch);
    if (!status.ok())
      throw std::runtime_error("Long batch finish failed: " + status.ToString());

    auto table = arrow::Table::FromRecordBatches({batch});
    if (!table.ok())
      throw std::runtime_error("Long table create failed: " + table.status().ToString());

    auto write_status = long_writer->WriteTable(*(*table), chunk_size);
    if (!write_status.ok())
      throw std::runtime_error("Long write failed: " + write_status.ToString());

    long_buffered = 0;
    long_appenders.clear();  // Phase 5: will be rebuilt
    long_fast_initialized_ = false;  // long-table fast path: re-cache after reset
    auto reset_status = reset_builders(long_schema, long_builders);
    if (!reset_status.ok())
      throw std::runtime_error("Long builder reset failed: " + reset_status.ToString());
  }

  void append_long(Row row) {
    if (!write_long) return;
    // Phase 5: lazy-init appenders
    if (long_appenders.empty() && !long_builders.empty()) {
      long_appenders = make_appenders(long_schema, long_builders);
    }
    auto status = append_row(row, long_col_index, long_builders, long_schema, long_appenders, static_cast<int>(long_count_));
    if (!status.ok())
      throw std::runtime_error("Long append failed: " + status.ToString());
    ++long_buffered;
    ++long_count_;
    if (long_buffered >= chunk_size) flush_long();
  }

  /// Long-table fast path: direct typed builder append.
  /// No map, no vector, no col_index, no hash lookup.
  void append_long_fast(const LongRowFixed& row) {
    if (!write_long) return;
    if (!use_long_fast_) {
      // Fallback to legacy: convert to Row and append
      Row legacy_row;
      if (row.has_part_index())
        legacy_row["part_index"] = CellValue::int32(row.part_index);
      else
        legacy_row["part_index"] = CellValue::null();
      legacy_row["attempt_index"]   = CellValue::int32(row.attempt_index);
      legacy_row["test_num"]        = CellValue::int32(row.test_num);
      legacy_row["result"]          = CellValue::string(row.result);
      legacy_row["is_fail"]         = CellValue::boolean(row.is_fail);
      legacy_row["test_flag"]       = CellValue::int32(row.test_flag);
      legacy_row["param_flag"]      = CellValue::int32(row.param_flag);
      legacy_row["record_sequence"]  = CellValue::int32(row.record_sequence);
      legacy_row["record_type"]     = CellValue::string(row.record_type);
      if (row.result_index >= 0)
        legacy_row["result_index"]  = CellValue::int32(row.result_index);
      else
        legacy_row["result_index"]  = CellValue::null();
      legacy_row["test_item_key"]   = CellValue::binary(row.test_item_key);
      append_long(std::move(legacy_row));
      return;
    }

    // Lazy-init typed builder cache
    if (!long_fast_initialized_ && !long_builders.empty()) {
      init_long_fast_builders();
      auto status = validate_long_fast_schema();
      if (!status.ok()) {
        DY_LOG_ERROR(std::string("Long fast schema validation failed: ") + status.ToString());
        throw std::runtime_error("Long fast schema validation failed: " + status.ToString());
      }
    }

    auto& b = long_fast_builders_;

    // part_index: append null if not set (sentinel -1)
    if (row.has_part_index())
      b.part_index->Append(row.part_index).ok();
    else
      b.part_index->AppendNull().ok();

    b.attempt_index->Append(row.attempt_index).ok();
    b.test_num->Append(row.test_num).ok();

    // result: string, can be empty
    b.result->Append(row.result).ok();

    b.is_fail->Append(row.is_fail).ok();
    b.test_flag->Append(row.test_flag).ok();
    b.param_flag->Append(row.param_flag).ok();
    b.record_sequence->Append(row.record_sequence).ok();
    b.record_type->Append(row.record_type).ok();
    if (row.result_index >= 0)
      b.result_index->Append(row.result_index).ok();
    else
      b.result_index->AppendNull().ok();
    b.test_item_key->Append(row.test_item_key.data()).ok();

    ++long_buffered;
    ++long_count_;
    if (long_buffered >= chunk_size) flush_long();
  }

  // Convert a name-keyed Row to a ColumnId-keyed SparseRow (legacy path; the
  // hot path uses append_wide_all_sparse directly).  Requires set_column_info.
  SparseRow row_to_sparse(Row row) const {
    SparseRow sparse;
    sparse.reserve(row.size());
    for (auto& [name, value] : row) {
      auto it = name_to_id_.find(name);
      if (it != name_to_id_.end()) sparse[it->second] = std::move(value);
    }
    return sparse;
  }

  void append_wide_all(Row row) {
    wide_all_sparse_rows_.push_back(row_to_sparse(std::move(row)));
    ++wide_all_count_;
  }

  void append_wide_all_sparse(SparseRow row) {
    wide_all_sparse_rows_.push_back(std::move(row));
    ++wide_all_count_;
  }

  void set_column_info(std::vector<std::string> id_to_name, std::uint32_t attempt_key_id) {
    id_to_name_ = std::move(id_to_name);
    attempt_key_id_ = attempt_key_id;
    name_to_id_.clear();
    name_to_id_.reserve(id_to_name_.size());
    for (std::size_t i = 0; i < id_to_name_.size(); ++i) {
      name_to_id_[id_to_name_[i]] = static_cast<std::uint32_t>(i);
    }
  }

  // ColumnId-keyed lookups used by ft_facts / ews index / final selection.
  const CellValue* sparse_find(const SparseRow& row, const char* name) const {
    auto it = name_to_id_.find(name);
    if (it == name_to_id_.end()) return nullptr;
    auto vit = row.find(it->second);
    return vit == row.end() ? nullptr : &vit->second;
  }
  std::string sparse_string(const SparseRow& row, std::initializer_list<const char*> names) const {
    for (const auto* name : names) {
      const auto* v = sparse_find(row, name);
      if (v && v->type == CellValue::String && !v->str_val.empty()) return v->str_val;
    }
    return {};
  }
  std::optional<std::int32_t> sparse_int(const SparseRow& row, std::initializer_list<const char*> names) const {
    for (const auto* name : names) {
      const auto* v = sparse_find(row, name);
      if (v && v->type == CellValue::Int32) return v->int_val;
    }
    return std::nullopt;
  }

  std::vector<Row> build_ft_execution_facts() const {
    struct Acc {
      std::string source_file;
      std::string lot_id;
      std::string program_name;
      std::string start_time;
      std::string finish_time;
      int total = 0;
      int valid = 0;
      int pass = 0;
      int fail = 0;
      int abort = 0;
      int unknown = 0;
      std::map<std::string, int> hbins;
      std::map<std::string, int> sbins;
      std::set<std::string> reasons;
    };

    auto string_value = [](const Row& row, const std::string& key) -> std::string {
      const auto it = row.find(key);
      return it != row.end() && it->second.type == CellValue::String ? it->second.str_val : "";
    };
    auto int_value = [](const Row& row, const std::string& key) -> std::optional<int> {
      const auto it = row.find(key);
      if (it == row.end() || it->second.type != CellValue::Int32) return std::nullopt;
      return it->second.int_val;
    };
    auto json_map = [](const std::map<std::string, int>& values) {
      std::ostringstream out;
      out << "{";
      bool first = true;
      for (const auto& [key, value] : values) {
        if (!first) out << ",";
        first = false;
        write_json_string(out, key);
        out << ":" << value;
      }
      out << "}";
      return out.str();
    };
    auto json_reasons = [](const std::set<std::string>& values) {
      std::ostringstream out;
      out << "[";
      bool first = true;
      for (const auto& value : values) {
        if (!first) out << ",";
        first = false;
        write_json_string(out, value);
      }
      out << "]";
      return out.str();
    };

    std::map<std::string, Acc> grouped;
    for (const auto& row : wide_all_sparse_rows_) {
      const auto segment = sparse_string(row, {"__segment_id"}).empty()
          ? std::string("segment-0001") : sparse_string(row, {"__segment_id"});
      auto& fact = grouped[segment];
      if (fact.source_file.empty()) fact.source_file = sparse_string(row, {"__source_file"});
      if (fact.lot_id.empty()) fact.lot_id = sparse_string(row, {"LOT_ID"});
      if (fact.program_name.empty()) fact.program_name = sparse_string(row, {"test_program_name"});
      if (fact.start_time.empty()) fact.start_time = sparse_string(row, {"start_time"});
      if (fact.finish_time.empty()) fact.finish_time = sparse_string(row, {"finish_time"});

      std::string classification = "UNKNOWN";
      const auto flag = sparse_int(row, {"__part_flag"});
      if (flag) {
        if ((*flag & 0xE0) != 0) fact.reasons.insert("PART_FLAG_RESERVED_BITS_SET");
        if ((*flag & 0x04) != 0) classification = "ABORT";
        else if ((*flag & 0x10) != 0) classification = "UNKNOWN";
        else if ((*flag & 0x08) != 0) classification = "FAIL";
        else classification = "PASS";
      } else {
        const auto fallback = sparse_string(row, {"__pass_fail"});
        if (fallback == "PASS" || fallback == "FAIL") classification = fallback;
      }
      ++fact.total;
      if (classification == "PASS") { ++fact.pass; ++fact.valid; }
      else if (classification == "FAIL") { ++fact.fail; ++fact.valid; }
      else if (classification == "ABORT") ++fact.abort;
      else ++fact.unknown;

      const auto hbin = sparse_int(row, {"HBIN"});
      const auto sbin = sparse_int(row, {"SBIN"});
      if (hbin) ++fact.hbins[std::to_string(*hbin)];
      if (sbin) ++fact.sbins[std::to_string(*sbin)];
    }

    std::vector<Row> result;
    result.reserve(grouped.size());
    for (const auto& [segment, fact] : grouped) {
      Row row;
      row["schema_version"] = CellValue::string("ft-execution-fact-v1");
      row["source_asset_id"] = CellValue::string(fact.source_file.empty() ? "unknown-source" : fact.source_file);
      row["source_sha256"] = CellValue::string("");
      row["source_file"] = CellValue::string(fact.source_file);
      row["segment_id"] = CellValue::string(segment);
      row["project_id"] = CellValue::null();
      row["product_id"] = CellValue::null();
      row["lot_id"] = fact.lot_id.empty() ? CellValue::null() : CellValue::string(fact.lot_id);
      row["program_name"] = fact.program_name.empty() ? CellValue::null() : CellValue::string(fact.program_name);
      row["program_revision"] = CellValue::null();
      row["start_time_utc"] = fact.start_time.empty() ? CellValue::null() : CellValue::string(fact.start_time);
      row["finish_time_utc"] = fact.finish_time.empty() ? CellValue::null() : CellValue::string(fact.finish_time);
      row["source_timezone"] = CellValue::string("Asia/Shanghai");
      row["total_prr"] = CellValue::int32(fact.total);
      row["valid_prr"] = CellValue::int32(fact.valid);
      row["pass_prr"] = CellValue::int32(fact.pass);
      row["fail_prr"] = CellValue::int32(fact.fail);
      row["abort_prr"] = CellValue::int32(fact.abort);
      row["unknown_prr"] = CellValue::int32(fact.unknown);
      row["hbin_counts_json"] = CellValue::string(json_map(fact.hbins));
      row["sbin_counts_json"] = CellValue::string(json_map(fact.sbins));
      row["result_source"] = CellValue::string("PRR_PART_FLAG");
      row["parser_version"] = CellValue::string(kParserVersion);
      row["result_policy_id"] = CellValue::string("stdf-prr-part-flag-v1");
      row["result_policy_status"] = CellValue::string("CONFIGURED");
      row["reason_codes_json"] = CellValue::string(json_reasons(fact.reasons));
      result.push_back(std::move(row));
    }
    if (result.empty()) {
      Row empty;
      empty["schema_version"] = CellValue::string("ft-execution-fact-v1");
      empty["source_asset_id"] = CellValue::string("unknown-source");
      empty["source_sha256"] = CellValue::string("");
      empty["source_file"] = CellValue::string("");
      empty["segment_id"] = CellValue::string("segment-0001");
      empty["source_timezone"] = CellValue::string("Asia/Shanghai");
      empty["total_prr"] = CellValue::int32(0);
      empty["valid_prr"] = CellValue::int32(0);
      empty["pass_prr"] = CellValue::int32(0);
      empty["fail_prr"] = CellValue::int32(0);
      empty["abort_prr"] = CellValue::int32(0);
      empty["unknown_prr"] = CellValue::int32(0);
      empty["hbin_counts_json"] = CellValue::string("{}");
      empty["sbin_counts_json"] = CellValue::string("{}");
      empty["result_source"] = CellValue::string("PRR_PART_FLAG");
      empty["parser_version"] = CellValue::string(kParserVersion);
      empty["result_policy_id"] = CellValue::string("stdf-prr-part-flag-v1");
      empty["result_policy_status"] = CellValue::string("CONFIGURED");
      empty["reason_codes_json"] = CellValue::string("[]");
      result.push_back(std::move(empty));
    }
    return result;
  }

  void write_ft_execution_facts() {
    auto rows = build_ft_execution_facts();
    auto partial = parquet_dir / "ft_execution_facts.parquet.partial";
    auto final = parquet_dir / "ft_execution_facts.parquet";
    write_wide_parquet(rows, partial, FT_EXECUTION_FACT_COLUMNS);
    std::error_code ec;
    fs::rename(partial, final, ec);
    if (ec) {
      fs::copy(partial, final, ec);
      fs::remove(partial, ec);
    }
    ft_execution_fact_count_ = static_cast<std::int64_t>(rows.size());
  }

  std::vector<Row> build_ews_attempt_index() const {
    std::vector<Row> result;
    const auto count = std::min(wide_all_sparse_rows_.size(), part_summary_rows.size());
    result.reserve(count);

    auto state_from_row = [&](const SparseRow& row) {
      const auto explicit_state = sparse_string(row, {"__pass_fail", "attempt_state"});
      if (explicit_state == "PASS" || explicit_state == "FAIL" || explicit_state == "ABORT" ||
          explicit_state == "UNKNOWN" || explicit_state == "CONFLICT") return explicit_state;
      const auto flag = sparse_int(row, {"__part_flag", "part_flag"});
      if (!flag) return std::string("UNKNOWN");
      if ((*flag & 0x04) != 0) return std::string("ABORT");
      if ((*flag & 0x10) != 0) return std::string("UNKNOWN");
      if ((*flag & 0x08) != 0) return std::string("FAIL");
      return std::string("PASS");
    };

    for (std::size_t i = 0; i < count; ++i) {
      const auto& wide = wide_all_sparse_rows_[i];
      const auto& summary = part_summary_rows[i];
      Row row;
      const auto source_file = sparse_string(wide, {"__source_file", "source_file"});
      const auto product = sparse_string(wide, {"part_type", "device"});
      const auto lot = sparse_string(wide, {"LOT_ID", "Lot_ID", "lot_id"});
      const auto wafer = sparse_string(wide, {"WAFER_ID", "wafer_id"});
      const auto die_x = sparse_int(wide, {"DIE_X", "die_x"});
      const auto die_y = sparse_int(wide, {"DIE_Y", "die_y"});
      row["dataset_id"] = CellValue::string(source_file.empty() ? "native-stdf" : fs::path(source_file).filename().string());
      // The importer knows the canonical SHA-256 and replaces this pending
      // value while finalizing the index manifest.
      row["source_file_hash"] = CellValue::string("");
      row["source_record_sequence"] = summary.count("record_sequence") ? summary.at("record_sequence") : CellValue::int32(static_cast<std::int32_t>(i));
      row["part_index"] = summary.count("part_index") ? summary.at("part_index") : CellValue::null();
      row["attempt_index"] = summary.count("attempt_index") ? summary.at("attempt_index") : CellValue::null();
      row["project_id"] = CellValue::string("UNKNOWN");
      row["product_id"] = CellValue::string(product.empty() ? "UNKNOWN" : product);
      row["lot_id"] = CellValue::string(lot.empty() ? "UNKNOWN" : lot);
      row["wafer_id"] = CellValue::string(wafer.empty() ? "UNKNOWN" : wafer);
      row["coordinate_system_id"] = CellValue::string("PRR");
      row["die_x"] = die_x ? CellValue::int32(*die_x) : CellValue::null();
      row["die_y"] = die_y ? CellValue::int32(*die_y) : CellValue::null();
      const auto site = sparse_int(wide, {"SITE", "site"});
      row["site"] = site ? CellValue::string(std::to_string(*site)) :
          (summary.count("site_num") && summary.at("site_num").type == CellValue::Int32
              ? CellValue::string(std::to_string(summary.at("site_num").int_val)) : CellValue::null());
      row["head"] = sparse_int(wide, {"SDR_HEAD_NUM", "head"}) ? CellValue::int32(*sparse_int(wide, {"SDR_HEAD_NUM", "head"})) : CellValue::null();
      row["attempt_state"] = CellValue::string(state_from_row(wide));
      row["test_start_time"] = sparse_string(wide, {"start_time", "test_start_time"}).empty() ? CellValue::null() : CellValue::string(sparse_string(wide, {"start_time", "test_start_time"}));
      row["test_end_time"] = sparse_string(wide, {"finish_time", "test_end_time"}).empty() ? CellValue::null() : CellValue::string(sparse_string(wide, {"finish_time", "test_end_time"}));
      row["hbin"] = sparse_int(wide, {"HBIN", "hbin"}) ? CellValue::int32(*sparse_int(wide, {"HBIN", "hbin"})) : CellValue::null();
      row["sbin"] = sparse_int(wide, {"SBIN", "sbin"}) ? CellValue::int32(*sparse_int(wide, {"SBIN", "sbin"})) : CellValue::null();
      const auto program = sparse_string(wide, {"test_program_name", "program_key"});
      row["program_key"] = CellValue::string(program.empty() ? "UNKNOWN" : program);
      row["ordering_evidence"] = CellValue::string("EXACT");
      row["attribution_quality"] = (die_x && die_y) ? CellValue::string("EXACT") : CellValue::string("UNAVAILABLE");
      result.push_back(std::move(row));
    }
    return result;
  }

  void write_ews_attempt_index() {
    auto rows = build_ews_attempt_index();
    auto partial = parquet_dir / "ews_attempt_index.parquet.partial";
    auto final = parquet_dir / "ews_attempt_index.parquet";
    write_wide_parquet(rows, partial, EWS_ATTEMPT_INDEX_COLUMNS);
    std::error_code ec;
    fs::rename(partial, final, ec);
    if (ec) {
      fs::copy(partial, final, ec);
      fs::remove(partial, ec);
    }
    ews_attempt_count_ = static_cast<std::int64_t>(rows.size());
  }

  void append_catalog(Row row) {
    catalog_rows.push_back(std::move(row));
    ++catalog_count_;
  }

  void write_wide_parquet(const std::vector<Row>& rows,
                          const fs::path& path,
                          const std::vector<std::string>& columns) {
    std::string fname = path.filename().string();
    const auto t_w0 = std::chrono::steady_clock::now();
    std::cerr << "[stdf2parquet] write_parquet " << fname << " rows=" << rows.size()
              << " cols=" << columns.size() << " schema...\n";
    std::cerr.flush();

    auto schema = make_wide_schema(columns, fname);
    std::cerr << "[stdf2parquet] write_parquet " << fname << " schema done, fields="
              << schema->num_fields() << "\n";
    std::cerr.flush();

    std::unordered_map<std::string, int> col_index;
    std::cerr << "[stdf2parquet] write_parquet " << fname << " build col_index begin\n";
    std::cerr.flush();
    for (std::size_t i = 0; i < columns.size(); ++i) {
      col_index[columns[i]] = static_cast<int>(i);
    }
    std::cerr << "[stdf2parquet] write_parquet " << fname << " col_index done size=" << col_index.size() << "\n";
    std::cerr.flush();

    std::cerr << "[stdf2parquet] write_parquet " << fname << " make builders begin\n";
    std::cerr.flush();
    std::cerr << "[stage] make_builders_start\n";
    std::cerr.flush();
    std::vector<std::shared_ptr<arrow::ArrayBuilder>> builders;
    auto reset_ok = reset_builders(schema, builders);
    if (!reset_ok.ok())
      throw std::runtime_error("Wide builder init failed: " + reset_ok.ToString());
    std::cerr << "[stdf2parquet] write_parquet " << fname << " builders ready count=" << builders.size() << "\n";
    std::cerr.flush();

    std::cerr << "[stage] make_builders_done builder_count=" << builders.size() << "\n";
    std::cerr.flush();

    // Phase 5: Build typed appenders (no dynamic_cast in append hot path)
    std::vector<ColumnAppender> appenders = make_appenders(schema, builders);

    // Debug stop: after builders
    if (g_debug.stop_after_builders) {
      DY_LOG_INFO("[stage] debug-stop-after-builders done, exiting");
      std::cerr.flush();
      std::exit(0);
    }

    auto out_result = arrow::io::FileOutputStream::Open(path.string());
    if (!out_result.ok())
      throw std::runtime_error("Cannot open wide Parquet: " + out_result.status().ToString());

    auto writer_result = parquet::arrow::FileWriter::Open(
        *schema, arrow::default_memory_pool(), *out_result,
        parquet::default_writer_properties(),
        parquet::default_arrow_writer_properties());
    if (!writer_result.ok())
      throw std::runtime_error("Cannot create wide Parquet writer: " + writer_result.status().ToString());
    auto writer = std::move(*writer_result);
    // Adaptive chunk size based on column count to limit peak memory
    // (same principle as wide_all batched write — plan §7.3)
    std::int64_t col_aware_chunk = std::max(std::int64_t(100),
        std::min(chunk_size, std::int64_t(3000000 / std::max(1, static_cast<int>(columns.size())))));
    std::cerr << "[stdf2parquet] write_parquet " << fname << " writer opened, appending " << rows.size()
              << " rows, chunk_size=" << col_aware_chunk << "\n";
    std::cerr.flush();

    std::int64_t buffered = 0;
    // Open crash trace and register Windows crash handler
    deepyield_stdf::trace::open_trace();
#ifdef _WIN32
    g_crash_log.open("stdf2parquet_native_crash.log", std::ios::out | std::ios::app);
    SetUnhandledExceptionFilter(unhandled_exception_filter);
#endif

    // Validate schema/builders consistency before append
    if (static_cast<int>(builders.size()) != schema->num_fields()) {
      std::cerr << "[stdf2parquet] FATAL builders=" << builders.size()
                << " schema_fields=" << schema->num_fields() << "\n";
      throw std::runtime_error("schema/builders count mismatch");
    }
    for (int i = 0; i < schema->num_fields(); ++i) {
      if (!builders[i]) {
        throw std::runtime_error("builder[" + std::to_string(i) + "] is null");
      }
    }
    // Log column counts — also flag suspicious columns (string in dynamic region)
    int identity_cols = 0, test_cols = 0;
    for (const auto& col : columns) {
      if (col == "Name" || col == "LOT_ID" || col == "WAFER_ID" || col == "SITE" ||
          col == "DIE_X" || col == "DIE_Y" || col == "HBIN" || col == "SBIN" ||
          col.find("__") == 0) {
        ++identity_cols;
      } else {
        ++test_cols;
        // Flag non-numeric-looking column names in the test region
        bool looks_metadata = false;
        for (const auto& keyword : {"LOT", "WAFER", "PART", "DEVICE", "SITE", "BIN",
                                     "TIME", "DATE", "SETUP", "PROGRAM", "NAME", "TEXT",
                                     "JOB", "NODE", "SBLOT", "FLOW", "FACILITY", "FLOOR",
                                     "OPERATOR", "ENGINEER", "SUPERVISOR", "TEST_COD",
                                     "SPEC", "PACKAGE", "FAMILY", "ROM", "SERIAL"}) {
          if (col.find(keyword) != std::string::npos) { looks_metadata = true; break; }
        }
        if (looks_metadata && test_cols <= 30) {
          std::cerr << "[stdf2parquet] SUSPICIOUS col=" << col
                    << " classified as test(double) but looks like metadata\n";
          std::cerr.flush();
        }
      }
    }
    std::cerr << "[stdf2parquet] write_parquet " << fname
              << " identity_cols=" << identity_cols << " test_cols=" << test_cols
              << " total=" << columns.size() << "\n";
    std::cerr.flush();

    int global_row = 0;
    const auto t_w_setup = std::chrono::steady_clock::now();  // schema + builders done
    std::cerr << "[stdf2parquet] write_parquet " << fname << " append_rows begin rows=" << rows.size() << "\n";
    std::cerr.flush();
    std::cerr << "[stage] append_cells_start rows=" << rows.size()
              << " columns=" << columns.size() << "\n";
    std::cerr.flush();
    try {
      for (const auto& row : rows) {
        if (global_row > 0 && (global_row % 500 == 0)) {
          DY_LOG_DEBUG("[stdf2parquet] write_parquet " + fname + " append row=" + std::to_string(global_row) + "/" + std::to_string(rows.size()));
        }
        deepyield_stdf::trace::write_trace("append_row_begin global=" + std::to_string(global_row)
                  + " row_keys=" + std::to_string(row.size()));
        auto status = append_row(row, col_index, builders, schema, appenders, global_row);
        if (!status.ok())
          throw std::runtime_error("Wide row append failed: " + status.ToString());
        deepyield_stdf::trace::write_trace("append_row_done global=" + std::to_string(global_row));
        ++global_row;
        ++buffered;
        if (buffered >= col_aware_chunk) {
          std::cerr << "[stdf2parquet] write_parquet " << fname
                    << " flushing batch, buffered=" << buffered << " row=" << global_row << "\n";
          std::cerr.flush();
          std::shared_ptr<arrow::RecordBatch> batch;
          auto finish_ok = finish_batch(schema, builders, &batch);
          if (!finish_ok.ok())
            throw std::runtime_error("Wide batch finish failed: " + finish_ok.ToString());
          auto table = arrow::Table::FromRecordBatches({batch});
          std::cerr << "[stage] parquet_write_start batch\n";
          std::cerr.flush();
          auto write_ok = writer->WriteTable(*(*table), col_aware_chunk);
          std::cerr << "[stage] parquet_write_done\n";
          std::cerr.flush();
          if (!write_ok.ok())
            throw std::runtime_error("Wide write failed: " + write_ok.ToString());
          buffered = 0;
          reset_ok = reset_builders(schema, builders);
          if (!reset_ok.ok())
            throw std::runtime_error("Wide builder reset failed: " + reset_ok.ToString());
          appenders.clear();  // Phase 5 fix: rebuild appenders after builder reset
          std::cerr << "[stdf2parquet] write_parquet " << fname << " batch flushed\n";
          std::cerr.flush();
        }
      }
      std::cerr << "[stdf2parquet] write_parquet " << fname << " append done total_rows=" << global_row << " buffered=" << buffered << "\n";
      std::cerr.flush();
    } catch (const std::exception& e) {
      std::cerr << "[stdf2parquet] write_parquet " << fname << " EXCEPTION at row=" << global_row << ": " << e.what() << "\n";
      std::cerr.flush();
      throw;
    } catch (...) {
      std::cerr << "[stdf2parquet] write_parquet " << fname << " UNKNOWN EXCEPTION at row=" << global_row << "\n";
      std::cerr.flush();
      throw;
    }

    std::cerr << "[stage] append_cells_done rows=" << global_row
              << " columns=" << columns.size() << "\n";
    std::cerr.flush();

    // Debug stop: after append
    if (g_debug.stop_after_append) {
      DY_LOG_INFO("[stage] debug-stop-after-append done, exiting");
      std::cerr.flush();
      std::exit(0);
    }

    if (buffered > 0) {
      std::cerr << "[stdf2parquet] write_parquet " << fname << " final flush begin buffered=" << buffered << "\n";
      std::cerr.flush();
      std::shared_ptr<arrow::RecordBatch> batch;
      auto finish_ok = finish_batch(schema, builders, &batch);
      if (!finish_ok.ok())
        throw std::runtime_error("Wide batch finish failed: " + finish_ok.ToString());
      std::cerr << "[stdf2parquet] write_parquet " << fname << " batch ready, WriteTable...\n";
      std::cerr.flush();
      auto table = arrow::Table::FromRecordBatches({batch});
      std::cerr << "[stage] parquet_write_start final\n";
      std::cerr.flush();
      auto write_ok = writer->WriteTable(*(*table), chunk_size);
      std::cerr << "[stage] parquet_write_done\n";
      std::cerr.flush();
      if (!write_ok.ok())
        throw std::runtime_error("Wide write failed: " + write_ok.ToString());
      std::cerr << "[stdf2parquet] write_parquet " << fname << " final flush done\n";
      std::cerr.flush();
    }

    const auto t_w_flush = std::chrono::steady_clock::now();  // append + flushes done
    std::cerr << "[stdf2parquet] write_parquet " << fname << " closing writer...\n";
    std::cerr.flush();
    auto close_ok = writer->Close();
    if (!close_ok.ok())
      throw std::runtime_error("Wide writer close failed: " + close_ok.ToString());
    const auto t_w_close = std::chrono::steady_clock::now();
    std::cerr << "[write_timing] " << fname
              << " setup_ms=" << std::chrono::duration<double, std::milli>(t_w_setup - t_w0).count()
              << " append_flush_ms=" << std::chrono::duration<double, std::milli>(t_w_flush - t_w_setup).count()
              << " close_ms=" << std::chrono::duration<double, std::milli>(t_w_close - t_w_flush).count()
              << " total_ms=" << std::chrono::duration<double, std::milli>(t_w_close - t_w0).count() << "\n";
    std::cerr.flush();
    std::cerr << "[stdf2parquet] write_parquet " << fname << " done\n";
    std::cerr.flush();
    deepyield_stdf::trace::close_trace();
#ifdef _WIN32
    if (g_crash_log.is_open()) g_crash_log.close();
    SetUnhandledExceptionFilter(nullptr);
#endif
  }

  // Batched wide_all Parquet write (plan §7.3 — Task 8).
  // Uses adaptive batch_rows, ZSTD level 1, .partial→rename.
  // ColumnId-keyed wide writer (final/wide.parquet).  Same schema/config as the
  // Row-based write_wide_parquet (default writer properties) but appends
  // directly from SparseRow via ColumnId → column-index resolution — no
  // string-keyed Row conversion at close.
  void write_wide_parquet_sparse(const std::vector<SparseRow>& rows,
                                 const fs::path& path,
                                 const std::vector<std::string>& columns) {
    std::string fname = path.filename().string();
    const auto t_w0 = std::chrono::steady_clock::now();
    auto schema = make_wide_schema(columns, fname);

    std::vector<int> id_to_index(id_to_name_.size(), -1);
    for (std::size_t i = 0; i < columns.size(); ++i) {
      auto it = name_to_id_.find(columns[i]);
      if (it != name_to_id_.end() && it->second < id_to_index.size()) {
        id_to_index[it->second] = static_cast<int>(i);
      }
    }

    auto out_result = arrow::io::FileOutputStream::Open(path.string());
    if (!out_result.ok())
      throw std::runtime_error("Cannot open wide Parquet: " + out_result.status().ToString());

    auto writer_result = parquet::arrow::FileWriter::Open(
        *schema, arrow::default_memory_pool(), *out_result,
        parquet::default_writer_properties(),
        parquet::default_arrow_writer_properties());
    if (!writer_result.ok())
      throw std::runtime_error("Cannot create wide Parquet writer: " + writer_result.status().ToString());
    auto writer = std::move(*writer_result);

    // Adaptive chunk size (same principle as the Row-based wide write)
    std::int64_t chunk = std::max(std::int64_t(100),
        std::min(chunk_size, std::int64_t(3000000 / std::max(1, static_cast<int>(columns.size())))));
    std::int64_t buffered = 0;
    int global_row = 0;

    std::vector<std::shared_ptr<arrow::ArrayBuilder>> builders;
    std::vector<ColumnAppender> appenders;
    auto reset_batch = [&]() {
      auto ok = reset_builders(schema, builders);
      if (!ok.ok())
        throw std::runtime_error("Wide builder init failed: " + ok.ToString());
      appenders = make_appenders(schema, builders);
    };
    reset_batch();

    for (const auto& row : rows) {
      auto status = append_row_sparse(row, id_to_index, builders, schema, appenders, global_row);
      if (!status.ok())
        throw std::runtime_error("Wide row append failed: " + status.ToString());
      ++global_row;
      ++buffered;
      if (buffered >= chunk) {
        std::shared_ptr<arrow::RecordBatch> batch;
        auto finish_ok = finish_batch(schema, builders, &batch);
        if (!finish_ok.ok())
          throw std::runtime_error("Wide batch finish failed: " + finish_ok.ToString());
        auto table = arrow::Table::FromRecordBatches({batch});
        auto write_ok = writer->WriteTable(*(*table), chunk);
        if (!write_ok.ok())
          throw std::runtime_error("Wide write failed: " + write_ok.ToString());
        buffered = 0;
        reset_batch();
      }
    }
    if (buffered > 0) {
      std::shared_ptr<arrow::RecordBatch> batch;
      auto finish_ok = finish_batch(schema, builders, &batch);
      if (!finish_ok.ok())
        throw std::runtime_error("Wide batch finish failed: " + finish_ok.ToString());
      auto table = arrow::Table::FromRecordBatches({batch});
      auto write_ok = writer->WriteTable(*(*table), chunk_size);
      if (!write_ok.ok())
        throw std::runtime_error("Wide write failed: " + write_ok.ToString());
    }
    auto close_ok = writer->Close();
    if (!close_ok.ok())
      throw std::runtime_error("Wide writer close failed: " + close_ok.ToString());
    std::cerr << "[write_timing] " << fname << "_sparse total_ms="
              << std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_w0).count() << "\n";
    std::cerr.flush();
  }

  void write_wide_all_batched(const std::vector<SparseRow>& rows,
                              const std::vector<std::string>& columns,
                              int test_item_count) {
    const auto t_wa0 = std::chrono::steady_clock::now();
    auto schema = make_wide_schema(columns);
    int column_count = static_cast<int>(columns.size());

    // Adaptive batch size (plan §7.3):
    //   wide_all_target_cells = 3,000,000
    //   batch_rows = max(1, 3_000_000 / test_item_count)
    int test_items = std::max(1, test_item_count);
    int batch_rows = std::max(1, 3000000 / test_items);

    // ZSTD level 1 writer properties (plan §7.4)
    auto writer_props = parquet::WriterProperties::Builder()
        .compression(arrow::Compression::ZSTD)
        ->compression_level(1)
        ->build();

    // ColumnId → output-column index (array index; no string lookups in the
    // per-cell hot path).
    std::vector<int> id_to_index(id_to_name_.size(), -1);
    for (std::size_t i = 0; i < columns.size(); ++i) {
      auto it = name_to_id_.find(columns[i]);
      if (it != name_to_id_.end() && it->second < id_to_index.size()) {
        id_to_index[it->second] = static_cast<int>(i);
      }
    }

    auto ps_partial = parquet_dir / "wide_all.parquet.partial";
    auto ps_final   = parquet_dir / "wide_all.parquet";

    auto out_result = arrow::io::FileOutputStream::Open(ps_partial.string());
    if (!out_result.ok())
      throw std::runtime_error("Cannot open wide_all.parquet: " + out_result.status().ToString());

    auto writer_result = parquet::arrow::FileWriter::Open(
        *schema, arrow::default_memory_pool(), *out_result,
        writer_props,
        parquet::default_arrow_writer_properties());
    if (!writer_result.ok())
      throw std::runtime_error("Cannot create wide_all writer: " + writer_result.status().ToString());
    auto writer = std::move(*writer_result);

    int flush_count = 0;
    std::int64_t total_rows = static_cast<std::int64_t>(rows.size());
    bool first_batch = true;
    bool stage_builders_start_logged = false;
    const auto t_wa_setup = std::chrono::steady_clock::now();  // schema + writer + index done
    for (std::int64_t batch_start = 0; batch_start < total_rows; batch_start += batch_rows) {
      std::int64_t batch_end = std::min(batch_start + batch_rows, total_rows);

      if (!stage_builders_start_logged) {
        std::cerr << "[stage] make_builders_start wide_all\n";
        std::cerr.flush();
        stage_builders_start_logged = true;
      }

      std::vector<std::shared_ptr<arrow::ArrayBuilder>> builders;
      auto reset_ok = reset_builders(schema, builders);
      if (!reset_ok.ok())
        throw std::runtime_error("Wide_all batch builders failed: " + reset_ok.ToString());

      // Phase 5: Typed appenders
      std::vector<ColumnAppender> appenders = make_appenders(schema, builders);

      if (first_batch) {
        std::cerr << "[stage] make_builders_done builder_count=" << builders.size() << " wide_all\n";
        std::cerr.flush();

        // Debug stop: after builders (wide_all)
        if (g_debug.stop_after_builders) {
          DY_LOG_INFO("[stage] debug-stop-after-builders done (wide_all), exiting");
          std::cerr.flush();
          std::exit(0);
        }

        std::cerr << "[stage] append_cells_start rows=" << total_rows
                  << " columns=" << column_count << " wide_all\n";
        std::cerr.flush();
        first_batch = false;
      }

      for (std::int64_t i = batch_start; i < batch_end; ++i) {
        auto status = append_row_sparse(rows[static_cast<std::size_t>(i)], id_to_index, builders, schema, appenders, static_cast<int>(i));
        if (!status.ok())
          throw std::runtime_error("Wide_all row append failed: " + status.ToString());
      }

      std::shared_ptr<arrow::RecordBatch> batch;
      auto finish_ok = finish_batch(schema, builders, &batch);
      if (!finish_ok.ok())
        throw std::runtime_error("Wide_all batch finish failed: " + finish_ok.ToString());

      auto table = arrow::Table::FromRecordBatches({batch});
      std::cerr << "[stage] parquet_write_start wide_all batch=" << flush_count << "\n";
      std::cerr.flush();
      auto write_ok = writer->WriteTable(*(*table), batch_rows);
      std::cerr << "[stage] parquet_write_done wide_all\n";
      std::cerr.flush();
      if (!write_ok.ok())
        throw std::runtime_error("Wide_all batch write failed: " + write_ok.ToString());

      ++flush_count;
    }

    // After all batches: check append stop and log append done
    if (total_rows > 0) {
      std::cerr << "[stage] append_cells_done rows=" << total_rows
                << " columns=" << column_count << " wide_all\n";
      std::cerr.flush();

      // Debug stop: after append (wide_all)
      if (g_debug.stop_after_append) {
        DY_LOG_INFO("[stage] debug-stop-after-append done (wide_all), exiting");
        std::cerr.flush();
        std::exit(0);
      }
    }

    const auto t_wa_flush = std::chrono::steady_clock::now();  // append + batch writes done
    auto close_ok = writer->Close();
    if (!close_ok.ok())
      throw std::runtime_error("Wide_all writer close failed: " + close_ok.ToString());
    const auto t_wa_close = std::chrono::steady_clock::now();
    std::cerr << "[write_timing] wide_all"
              << " setup_ms=" << std::chrono::duration<double, std::milli>(t_wa_setup - t_wa0).count()
              << " append_flush_ms=" << std::chrono::duration<double, std::milli>(t_wa_flush - t_wa_setup).count()
              << " close_ms=" << std::chrono::duration<double, std::milli>(t_wa_close - t_wa_flush).count()
              << " total_ms=" << std::chrono::duration<double, std::milli>(t_wa_close - t_wa0).count() << "\n";
    std::cerr.flush();

    // Atomic rename .partial → final (plan §8.1)
    {
      std::error_code ec;
      fs::rename(ps_partial, ps_final, ec);
      if (ec) {
        fs::copy(ps_partial, ps_final, ec);
        fs::remove(ps_partial, ec);
      }
    }

    // Estimate peak memory (approximate — rows in memory × estimated bytes per row)
    double peak_memory_mb = static_cast<double>(total_rows) *
        static_cast<double>(column_count * 16 + 256) / (1024.0 * 1024.0);

    // Log stats to stderr (plan §7.3 requirement)
    std::cerr << "[stdf2parquet] wide_all: test_item_count=" << test_item_count
              << " batch_rows=" << batch_rows
              << " flush_count=" << flush_count
              << " peak_memory_mb=" << std::fixed << std::setprecision(1) << peak_memory_mb
              << " total_rows=" << total_rows << "\n";
  }

  void write_catalog_parquet() {
    auto schema = make_catalog_schema();
    std::unordered_map<std::string, int> col_index;
    for (std::size_t i = 0; i < CATALOG_COLUMNS.size(); ++i) {
      col_index[CATALOG_COLUMNS[i]] = static_cast<int>(i);
    }

    std::vector<std::shared_ptr<arrow::ArrayBuilder>> builders;
    auto reset_ok = reset_builders(schema, builders);
    if (!reset_ok.ok())
      throw std::runtime_error("Catalog builder init failed: " + reset_ok.ToString());

    // Phase 5: Typed appenders
    std::vector<ColumnAppender> appenders = make_appenders(schema, builders);

    int cat_idx = 0;
    for (const auto& row : catalog_rows) {
      auto status = append_row(row, col_index, builders, schema, appenders, cat_idx++);
      if (!status.ok())
        throw std::runtime_error("Catalog append failed: " + status.ToString());
    }

    std::shared_ptr<arrow::RecordBatch> batch;
    auto finish_ok = finish_batch(schema, builders, &batch);
    if (!finish_ok.ok())
      throw std::runtime_error("Catalog batch finish failed: " + finish_ok.ToString());

    auto table = arrow::Table::FromRecordBatches({batch});
    if (!table.ok())
      throw std::runtime_error("Catalog table create failed: " + table.status().ToString());

    auto catalog_path = (parquet_dir / "test_catalog.parquet").string();
    auto out_result = arrow::io::FileOutputStream::Open(catalog_path);
    if (!out_result.ok())
      throw std::runtime_error("Cannot open catalog Parquet: " + out_result.status().ToString());

    auto writer_result = parquet::arrow::FileWriter::Open(
        *schema, arrow::default_memory_pool(), *out_result,
        parquet::default_writer_properties(),
        parquet::default_arrow_writer_properties());
    if (!writer_result.ok())
      throw std::runtime_error("Cannot create catalog writer: " + writer_result.status().ToString());

    auto writer = std::move(*writer_result);
    auto write_ok = writer->WriteTable(*(*table), chunk_size);
    if (!write_ok.ok())
      throw std::runtime_error("Catalog write failed: " + write_ok.ToString());

    auto close_ok = writer->Close();
    if (!close_ok.ok())
      throw std::runtime_error("Catalog close failed: " + close_ok.ToString());
  }

  std::vector<std::string> close_impl() {
    if (closed) return {};
    closed = true;
    std::cerr << "[stdf2parquet] close_impl begin, wide_all_rows=" << wide_all_sparse_rows_.size()
              << " write_long=" << write_long << " write_wide_all=" << write_wide_all << "\n";

    // Flush remaining long rows, close, and rename .partial → final
    auto t_long_start = std::chrono::steady_clock::now();
    if (write_long) flush_long();
    if (long_writer) {
      auto close_status = long_writer->Close();
      if (!close_status.ok())
        throw std::runtime_error("Long writer close failed: " + close_status.ToString());
    }
    long_writer.reset();
    long_out.reset();
    // Atomically rename .partial → final (plan §8.1)
    {
      auto lp_partial = parquet_dir / "long.parquet.partial";
      auto lp_final   = parquet_dir / "long.parquet";
      if (fs::exists(lp_partial)) {
        std::error_code ec;
        fs::rename(lp_partial, lp_final, ec);
        if (ec) {
          fs::copy(lp_partial, lp_final, ec);
          fs::remove(lp_partial, ec);
        }
      }
    }
    if (write_long) {
      write_long_sec_ = std::chrono::duration<double>(
          std::chrono::steady_clock::now() - t_long_start).count();
    }

    // Determine wide columns from all wide_all rows (ColumnId-keyed)
    std::vector<std::string> all_columns = ordered_wide_columns_sparse(wide_all_sparse_rows_, id_to_name_);

    // FT execution facts are built before wide_all rows are moved into the final table.
    // They are an additive parser product and do not change wide/final-attempt semantics.
    write_ft_execution_facts();
    // EWS attempt facts are built from the in-memory PRR/part rows before
    // final-wide extraction releases the source rows. The canonical source
    // hash is filled by Python asset finalization.
    write_ews_attempt_index();

    // Debug stop: after schema (build + validate + exit, before any builders/append)
    if (g_debug.stop_after_schema) {
      std::cerr << "[stage] build_fields_start\n";
      std::cerr.flush();
      auto schema = make_wide_schema(all_columns);
      // make_wide_schema already does validate_fields + arrow::schema + schema->Validate()
      // and prints all stage logs internally.
      std::cerr << "[stage] debug-stop-after-schema done, exiting\n";
      std::cerr.flush();
      std::exit(0);
    }

    // Write wide_all.parquet with adaptive batching (plan §7.3 — Task 8).
    // Uses ZSTD level 1, .partial → rename, and logs batch statistics.
    // Fast assetization skips this table entirely.
    if (write_wide_all) {
      auto t_wa_start = std::chrono::steady_clock::now();
      int test_items = static_cast<int>(catalog_rows.size());
      write_wide_all_batched(wide_all_sparse_rows_, all_columns, test_items);
      write_wide_all_sec_ = std::chrono::duration<double>(
          std::chrono::steady_clock::now() - t_wa_start).count();
    }

    // Compute final-wide rows (last by __attempt_key).
    // Also capture latest indices for part_summary is_final computation.
    std::vector<SparseRow> final_sparse;
    std::set<std::size_t> latest_indices;  // indices of final attempt per die
    {
      std::unordered_map<std::string, std::size_t> latest;
      for (std::size_t i = 0; i < wide_all_sparse_rows_.size(); ++i) {
        const auto* v = sparse_find(wide_all_sparse_rows_[i], "__attempt_key");
        if (v && v->type == CellValue::String) latest[v->str_val] = i;
      }
      // Save the set of final-version indices
      for (const auto& [key, idx] : latest) {
        (void)key;
        latest_indices.insert(idx);
      }

      // Extract the final sparse rows (last attempt per die).
      if (latest.empty()) {
        // No retest: swap ownership instead of copy → zero extra peak memory
        final_sparse.swap(wide_all_sparse_rows_);
      } else {
        final_sparse.reserve(latest.size());
        // Phase 6: Move rows instead of deep-copying
        // Sort indices for safe extraction (move from end to preserve earlier indices)
        std::vector<std::size_t> sorted_indices;
        for (const auto& [key, idx] : latest) {
          (void)key;
          sorted_indices.push_back(idx);
        }
        std::sort(sorted_indices.begin(), sorted_indices.end(), std::greater<std::size_t>());
        for (auto idx : sorted_indices) {
          final_sparse.push_back(std::move(wide_all_sparse_rows_[idx]));
          ++g_perf().row_move_count;
        }
        // Release wide_all_sparse_rows immediately after extracting final rows
        std::vector<SparseRow>().swap(wide_all_sparse_rows_);
        std::cerr << "[stdf2parquet] wide_all_sparse_rows released after final extraction, final_rows="
                  << final_sparse.size() << "\n";
      }
    }

    // Remove __attempt_key from final-wide columns
    std::vector<std::string> final_columns;
    for (const auto& col : all_columns) {
      if (col != "__attempt_key") final_columns.push_back(col);
    }

    // Strip __attempt_key and write wide.parquet directly from sparse rows
    // (no string-keyed Row conversion at close).
    for (auto& srow : final_sparse) srow.erase(attempt_key_id_);
    std::cerr << "[stdf2parquet] writing wide.parquet, final_rows=" << final_sparse.size()
              << " columns=" << final_columns.size() << "\n";
    write_wide_parquet_sparse(final_sparse, parquet_dir / "wide.parquet", final_columns);
    wide_count_ = static_cast<std::int64_t>(final_sparse.size());
    std::cerr << "[stdf2parquet] wide.parquet done\n";

    // ---- Write part_summary.parquet (plan §5.4 — Task 6) ----
    // Only written for Stage 2 (materialize / full), not for overview (fast).
    // Mark is_final using the latest_indices computed above.
    // Write with .partial → rename atomically (plan §8.1).
    auto t_ps_start = std::chrono::steady_clock::now();
    if (write_wide_all) {
      std::vector<std::string> ps_columns;
      for (const auto& col : PART_SUMMARY_COLUMNS) ps_columns.push_back(col);

      for (std::size_t i = 0; i < part_summary_rows.size(); ++i) {
        auto& row = part_summary_rows[i];
        row["is_final"] = CellValue::boolean(latest_indices.count(i) > 0);
        row.erase("__attempt_key");  // internal field, not part of schema
      }

      auto ps_partial = parquet_dir / "part_summary.parquet.partial";
      auto ps_final   = parquet_dir / "part_summary.parquet";
      write_wide_parquet(part_summary_rows, ps_partial, ps_columns);
      std::error_code ec;
      fs::rename(ps_partial, ps_final, ec);
      if (ec) {
        fs::copy(ps_partial, ps_final, ec);
        fs::remove(ps_partial, ec);
      }
      part_summary_count_ = static_cast<std::int64_t>(part_summary_rows.size());
    }
    write_part_summary_sec_ = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t_ps_start).count();

    // Write catalog
    std::cerr << "[stdf2parquet] writing test_catalog.parquet\n";
    write_catalog_parquet();

    std::cerr << "[stdf2parquet] close_impl done\n";
    return final_columns;
  }

};  // Impl

// ---------- public API ----------

ParquetAssetWriter::ParquetAssetWriter(const fs::path& output_dir,
                                       std::int64_t chunk_size,
                                       bool write_long,
                                       bool write_wide_all)
    : impl_(std::make_unique<Impl>(output_dir, chunk_size, write_long, write_wide_all)) {}

ParquetAssetWriter::~ParquetAssetWriter() = default;

void ParquetAssetWriter::open() { impl_->open(); }
void ParquetAssetWriter::append_long(Row row) { impl_->append_long(std::move(row)); }
void ParquetAssetWriter::append_long_fast(const LongRowFixed& row) { impl_->append_long_fast(row); }
void ParquetAssetWriter::set_long_writer_mode(const std::string& mode) {
  impl_->use_long_fast_ = (mode == "fast");
}
void ParquetAssetWriter::append_wide_all(Row row) { impl_->append_wide_all(std::move(row)); }
void ParquetAssetWriter::append_wide_all_sparse(SparseRow row) { impl_->append_wide_all_sparse(std::move(row)); }
void ParquetAssetWriter::set_column_info(std::vector<std::string> id_to_name, std::uint32_t attempt_key_id) {
  impl_->set_column_info(std::move(id_to_name), attempt_key_id);
}
void ParquetAssetWriter::append_catalog(Row row) { impl_->append_catalog(std::move(row)); }
void ParquetAssetWriter::append_part_summary(Row row) {
  impl_->part_summary_rows.push_back(std::move(row));
}

std::vector<std::string> ParquetAssetWriter::close() { return impl_->close_impl(); }

std::int64_t ParquetAssetWriter::wide_all_count() const { return impl_->wide_all_count_; }
std::int64_t ParquetAssetWriter::long_count() const { return impl_->long_count_; }
std::int64_t ParquetAssetWriter::catalog_count() const { return impl_->catalog_count_; }
std::int64_t ParquetAssetWriter::wide_count() const { return impl_->wide_count_; }
std::int64_t ParquetAssetWriter::part_summary_count() const { return impl_->part_summary_count_; }
std::int64_t ParquetAssetWriter::ft_execution_fact_count() const { return impl_->ft_execution_fact_count_; }

double ParquetAssetWriter::write_long_sec() const { return impl_->write_long_sec_; }
double ParquetAssetWriter::write_wide_all_sec() const { return impl_->write_wide_all_sec_; }
double ParquetAssetWriter::write_part_summary_sec() const { return impl_->write_part_summary_sec_; }

void ParquetAssetWriter::set_debug_config(const DebugConfig& cfg) {
  g_debug = cfg;
}

void ParquetAssetWriter::set_manifest_status(const std::string& status) {
  impl_->manifest_status_ = status;
}

void ParquetAssetWriter::write_manifest(
    const std::string& source_file,
    const std::string& source_type,
    std::uint64_t file_size,
    const std::string& file_hash,
    const std::map<std::string, int>& record_counts,
    const std::map<std::string, std::string>& stdf_metadata,
    const std::vector<std::string>& warnings,
    const std::vector<std::string>& wide_columns) {

  fs::path manifest_path = impl_->output_dir / "manifest.json";
  std::ofstream out(manifest_path, std::ios::binary);
  if (!out) throw std::runtime_error("Unable to write manifest.json");

  auto write_kv = [&](const std::string& key, const std::string& value, bool quote_value = true) {
    write_json_string(out, key);
    out << ":";
    if (quote_value)
      write_json_string(out, value);
    else
      out << value;
  };

  auto write_str_map = [&](const std::map<std::string, std::string>& m) {
    out << "{";
    bool first = true;
    for (const auto& [k, v] : m) {
      if (!first) out << ",";
      first = false;
      write_kv(k, v);
    }
    out << "}";
  };

  auto write_int_map = [&](const std::map<std::string, int>& m) {
    out << "{";
    bool first = true;
    for (const auto& [k, v] : m) {
      if (!first) out << ",";
      write_kv(k, std::to_string(v), false);
      first = false;
    }
    out << "}";
  };

  auto write_str_array = [&](const std::vector<std::string>& arr) {
    out << "[";
    bool first = true;
    for (const auto& v : arr) {
      if (!first) out << ",";
      first = false;
      write_json_string(out, v);
    }
    out << "]";
  };

  fs::path source_path(source_file);
  std::string display_name = source_path.filename().string();

  out << "{";

  write_kv("display_name", display_name);
  out << ",";
  write_kv("source_file_name", source_path.filename().string());
  out << ",";
  write_kv("source_file", source_file);
  out << ",";
  write_kv("raw_path", source_file);
  out << ",";
  write_kv("source_type", source_type);
  out << ",";
  write_kv("file_size", std::to_string(file_size), false);
  out << ",";
  write_kv("file_hash", file_hash);
  out << ",";
  write_kv("source_file_hash", file_hash);
  out << ",";
  write_kv("parser_version", kParserVersion);
  out << ",";
  write_kv("parquet_schema_version", kSchemaVersion);
  out << ",";
  write_kv("long_schema_version", impl_->write_long ? "compact_long_v2" : "");
  out << ",\"created_at\":\"" << now_isoish() << "\",";
  out << "\"processed_at\":\"" << now_isoish() << "\",";
  write_kv("status", impl_->manifest_status_);
  out << ",";
  // fully_materialized flag (plan §11 — Task 5)
  out << "\"fully_materialized\":"
      << (impl_->manifest_status_ == "fully_materialized" ? "true" : "false") << ",";
  write_kv("parquet_path", "parquet/wide.parquet");
  out << ",";
  // tables block (plan §11.1)
  out << "\"tables\":{";
  write_kv("wide", "parquet/wide.parquet");
  out << ",\"wide_ready\":true";
  if (impl_->write_wide_all) {
    out << ",";
    write_kv("wide_all", "parquet/wide_all.parquet");
    out << ",\"wide_all_ready\":true";
  }
  if (impl_->write_long) {
    out << ",";
    write_kv("long", "parquet/long.parquet");
    out << ",\"long_ready\":true";
  }
  if (impl_->write_wide_all) {
    out << ",";
    write_kv("part_summary", "parquet/part_summary.parquet");
    out << ",\"part_summary_ready\":true";
  }
  out << ",";
  write_kv("test_catalog", "parquet/test_catalog.parquet");
  out << ",\"test_catalog_ready\":true";
  out << ",";
  write_kv("ft_execution_facts", "parquet/ft_execution_facts.parquet");
  out << ",\"ft_execution_facts_ready\":true";
  if (impl_->ews_attempt_count_ > 0) {
    out << ",";
    write_kv("ews_attempt_index", "parquet/ews_attempt_index.parquet");
    out << ",\"ews_attempt_index_ready\":false";
  }
  out << "},";
  out << "\"capabilities\":{";
  write_kv("long_v2", impl_->write_long ? "true" : "false", false);
  out << ",";
  write_kv("ft_execution_fact_ready", "true", false);
  out << ",";
  write_kv("ft_execution_fact_schema", "ft-execution-fact-v1");
  out << ",";
  write_kv("ews_attempt_index_ready", impl_->ews_attempt_count_ > 0 ? "false" : "false", false);
  out << ",";
  write_kv("ews_attempt_index_schema", "yield-index-v1");
  out << "},";
  // paths block (legacy; kept for backward compatibility with Python loader)
  out << "\"paths\":{";
  write_kv("wide", "parquet/wide.parquet");
  if (impl_->write_wide_all) {
    out << ",";
    write_kv("wide_all", "parquet/wide_all.parquet");
  }
  if (impl_->write_long) {
    out << ",";
    write_kv("long", "parquet/long.parquet");
  }
  if (impl_->write_wide_all) {
    out << ",";
    write_kv("part_summary", "parquet/part_summary.parquet");
  }
  out << ",";
  write_kv("test_catalog", "parquet/test_catalog.parquet");
  out << ",";
  write_kv("ft_execution_facts", "parquet/ft_execution_facts.parquet");
  if (impl_->ews_attempt_count_ > 0) {
    out << ",";
    write_kv("ews_attempt_index", "parquet/ews_attempt_index.parquet");
  }
  out << "},";
  write_kv("row_count", std::to_string(impl_->wide_count_), false);
  out << ",";
  write_kv("column_count", std::to_string(static_cast<int>(wide_columns.size())), false);
  out << ",";
  out << "\"columns\":";
  write_str_array(wide_columns);
  out << ",";
  // test_item_count (plan §11.1 counts)
  out << "\"test_item_count\":" << std::to_string(impl_->catalog_count_) << ",";
  out << "\"row_counts\":{";
  write_kv("wide", std::to_string(impl_->wide_count_), false);
  if (impl_->write_wide_all) {
    out << ",";
    write_kv("wide_all", std::to_string(impl_->wide_all_count_), false);
  }
  if (impl_->write_long) {
    out << ",";
    write_kv("long", std::to_string(impl_->long_count_), false);
  }
  if (impl_->ews_attempt_count_ > 0) {
    out << ",";
    write_kv("ews_attempt_index", std::to_string(impl_->ews_attempt_count_), false);
  }
  if (impl_->write_wide_all) {
    out << ",";
    write_kv("part_summary", std::to_string(impl_->part_summary_count_), false);
  }
  out << ",";
  write_kv("test_catalog", std::to_string(impl_->catalog_count_), false);
  out << ",";
  write_kv("ft_execution_facts", std::to_string(impl_->ft_execution_fact_count_), false);
  out << "},";
  out << "\"record_counts\":";
  write_int_map(record_counts);
  out << ",";
  out << "\"stdf_metadata\":";
  write_str_map(stdf_metadata);
  out << ",";
  write_kv("test_start_time", "");
  out << ",";
  write_kv("test_finish_time", "");
  out << ",";
  out << "\"warnings\":";
  write_str_array(warnings);
  out << "}\n";
}

}  // namespace deepyield_stdf
