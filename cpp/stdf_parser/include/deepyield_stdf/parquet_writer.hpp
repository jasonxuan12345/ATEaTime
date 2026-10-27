#pragma once

#include <array>
#include <cstdint>
#if __has_include(<filesystem>)
#include <filesystem>
namespace fs = std::filesystem;
#elif __has_include(<experimental/filesystem>)
#include <experimental/filesystem>
namespace fs = std::experimental::filesystem;
#else
#error "No <filesystem> or <experimental/filesystem> available"
#endif
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace deepyield_stdf {

// ---------- typed cell ----------

struct CellValue {
  enum Type { Null, String, Int32, Double, Bool, Binary };
  Type type = Null;
  std::string str_val;
  std::int32_t int_val = 0;
  double dbl_val = 0.0;
  bool bool_val = false;
  std::vector<std::uint8_t> bin_val;

  static CellValue null() { return {}; }
  static CellValue string(std::string v) {
    CellValue c;
    c.type = String;
    c.str_val = std::move(v);
    return c;
  }
  static CellValue int32(std::int32_t v) {
    CellValue c;
    c.type = Int32;
    c.int_val = v;
    return c;
  }
  static CellValue double_val(double v) {
    CellValue c;
    c.type = Double;
    c.dbl_val = v;
    return c;
  }
  static CellValue boolean(bool v) {
    CellValue c;
    c.type = Bool;
    c.bool_val = v;
    return c;
  }
  static CellValue binary(const std::array<std::uint8_t, 16>& bytes) {
    CellValue c;
    c.type = Binary;
    c.bin_val.assign(bytes.begin(), bytes.end());
    return c;
  }

  bool is_null() const { return type == Null; }
};

// Row is name-keyed.  unordered_map (vs std::map) makes per-row map inserts
// in sparse_row_to_row ~3x faster; all consumers resolve via find() or
// set-based ordering, so the hash iteration order never affects output.
using Row = std::unordered_map<std::string, CellValue>;

// ColumnId-keyed row.  ColumnId == index into the ColumnRegistry's
// columns_in_order(); lets the writer resolve columns by array index instead
// of string lookups in the wide write hot path.
using SparseRow = std::unordered_map<std::uint32_t, CellValue>;

// ---------- long-table fixed-schema row (Phase 1 of long-table optimization) ----------

/// Fixed-structure row matching the current long.parquet schema.
/// Eliminates std::map/string/hash overhead in the long-format hot path.
///
/// Schema (verified 2026-06-09):
///   0: part_index     int32   (sentinel -1 = null / not yet set by PRR)
///   1: attempt_index  int32
///   2: test_num       int32
///   3: result         string
///   4: is_fail        bool
///   5: test_flag      int32
///   6: param_flag     int32
///   7: record_sequence int32
///   8: record_type    utf8        (v2)
///   9: result_index   int32 null  (v2; -1 means null)
///  10: test_item_key  fixed_size_binary(16) (v2)
struct LongRowFixed {
    std::int32_t part_index = -1;       // -1 means null
    std::int32_t attempt_index = 0;
    std::int32_t test_num = 0;
    std::string result;                  // PTR → formatted double, FTR → text
    bool is_fail = false;
    std::int32_t test_flag = 0;
    std::int32_t param_flag = 0;
    std::int32_t record_sequence = 0;
    std::string record_type;             // PTR / MPR / FTR
    std::int32_t result_index = -1;      // -1 means null (PTR/FTR)
    std::array<std::uint8_t, 16> test_item_key{};
    std::uint32_t column_id = 0;         // internal: catalog lookup at PRR close

    bool has_part_index() const { return part_index >= 0; }
};

// ---------- debug configuration ----------

struct DebugConfig {
  bool stop_after_schema = false;
  bool stop_after_builders = false;
  bool stop_after_append = false;
  bool stop_after_finish_arrays = false;
  bool stop_after_recordbatch = false;
  int schema_prefix = -1;  // -1 = use all fields
};

// ---------- debug / diagnostic helpers ----------

/// Standalone Arrow schema smoke test: build N float64 fields,
/// call arrow::schema(fields), validate, print logs, return 0/1.
int debug_arrow_schema_test(int num_fields);

/// Print build environment diagnostics (build type, Arrow version,
/// MSVC runtime info, cwd, argv) to stderr.
void print_env_diagnostics(int argc, char** argv);

// ---------- fixed column order ----------

extern const std::vector<std::string> LONG_COLUMNS;
extern const std::vector<std::string> CATALOG_COLUMNS;
extern const std::vector<std::string> PART_SUMMARY_COLUMNS;

// ---------- Parquet writer ----------

class ParquetAssetWriter {
 public:
  /// @param output_dir      Asset output directory (e.g. <asset>/)
  /// @param chunk_size      Rows per Parquet row group (default from DEEPYIELD_PARQUET_CHUNK_SIZE or 100000)
  /// @param write_long      Whether to materialize long.parquet.
  /// @param write_wide_all  Whether to materialize wide_all.parquet.
  ParquetAssetWriter(const fs::path& output_dir,
                     std::int64_t chunk_size = 0,
                     bool write_long = true,
                     bool write_wide_all = true);

  ~ParquetAssetWriter();

  // Non-copyable, movable
  ParquetAssetWriter(const ParquetAssetWriter&) = delete;
  ParquetAssetWriter& operator=(const ParquetAssetWriter&) = delete;

  /// Open parquet/ directory and initialise internal writers.
  void open();

  /// Append one long-format row (PTR/MPR/FTR). Written in row groups.
  /// Legacy path — uses generic Row/map; retained for fallback.
  void append_long(Row row);

  /// Append one long-format row using the fixed-schema fast path.
  /// Bypasses Row/map/vector/hash — writes directly to cached typed builders.
  /// @param row  Fixed-structure row matching long.parquet schema.
  void append_long_fast(const LongRowFixed& row);

  /// Set long-writer mode: "fast" (default) or "legacy".
  void set_long_writer_mode(const std::string& mode);

  /// Append one wide_all row (every PRR part). Buffered; flushed at close() so
  /// dynamic test columns can be discovered.
  void append_wide_all(Row row);

  /// Append one wide_all row from a ColumnId-keyed sparse structure (fast path —
  /// avoids building a string-keyed Row per PRR during parse).
  void append_wide_all_sparse(SparseRow row);

  /// Provide ColumnId → column-name metadata so close() can resolve ColumnId
  /// to output columns without string lookups.  Must be called before close().
  /// attempt_key_id is the ColumnId of the internal __attempt_key field.
  void set_column_info(std::vector<std::string> id_to_name,
                       std::uint32_t attempt_key_id);

  /// Register a test-catalog entry.
  void append_catalog(Row row);

  /// Append one part_summary row (plan §5.4 — Task 6).
  /// Buffered; is_final is computed at close() time.
  void append_part_summary(Row row);

  /// Compute final-only wide from buffered wide_all, flush all remaining data,
  /// write test_catalog, write part_summary, close all Parquet writers.
  /// Returns the column names used for wide.parquet.
  std::vector<std::string> close();

  // Row counts after close()
  std::int64_t wide_all_count() const;
  std::int64_t long_count() const;
  std::int64_t catalog_count() const;
  std::int64_t wide_count() const;
  std::int64_t part_summary_count() const;
  std::int64_t ft_execution_fact_count() const;

  /// Per-table write timing (plan §12 — Task 9). Valid after close().
  double write_long_sec() const;
  double write_wide_all_sec() const;
  double write_part_summary_sec() const;

  /// Configure debug stop points / schema prefix for crash isolation.
  void set_debug_config(const DebugConfig& cfg);

  /// Set the manifest status *before* calling write_manifest().
  /// For --profile overview this should be "overview_ready".
  /// For --profile materialize this should be "fully_materialized".
  /// Default is "succeeded" (legacy).
  void set_manifest_status(const std::string& status);

  /// Write manifest.json to output_dir. Must be called after close().
  void write_manifest(const std::string& source_file,
                      const std::string& source_type,
                      std::uint64_t file_size,
                      const std::string& file_hash,
                      const std::map<std::string, int>& record_counts,
                      const std::map<std::string, std::string>& stdf_metadata,
                      const std::vector<std::string>& warnings,
                      const std::vector<std::string>& wide_columns);

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace deepyield_stdf
