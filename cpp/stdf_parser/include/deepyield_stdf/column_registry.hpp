#pragma once
/// Column registry — caches test column names and assigns stable ColumnIds.
///
/// Phase 2 of the DeepYield STDF parser performance optimization plan.
/// Eliminates repeated test_column_name() calls in PTR/MPR hot paths.
///
/// Usage:
///   ColumnRegistry registry;
///   ColumnId id = registry.get_or_create_test_column(test_num, test_text, idx, CellValue::Double);
///   context.values[registry.meta(id).name] = ...;  // (string key until Phase 4)
///   // After Phase 4: context.row.values[id] = ...;

#include <cstdint>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "deepyield_stdf/parquet_writer.hpp"

namespace deepyield_stdf {

// ---- types ----

using ColumnId = std::uint32_t;

struct ColumnMeta {
    ColumnId id = 0;
    std::string name;
    CellValue::Type type = CellValue::Null;
    bool is_identity = false;
    bool is_meta = false;
    bool is_test = false;

    std::uint32_t test_num = 0;
    std::string test_text;
    std::uint32_t result_index = 0;
};

/// Composite key for test-column cache lookups.
struct TestColumnKey {
    std::uint32_t test_num;
    std::uint32_t result_index;

    bool operator==(const TestColumnKey& other) const noexcept {
        return test_num == other.test_num && result_index == other.result_index;
    }
};

struct TestColumnKeyHash {
    std::size_t operator()(const TestColumnKey& k) const noexcept {
        return (static_cast<std::size_t>(k.test_num) << 16) ^ static_cast<std::size_t>(k.result_index);
    }
};

// ---- ColumnRegistry ----

class ColumnRegistry {
public:
    ColumnRegistry() = default;

    /// Get or create a column id for a test result column.
    /// On cache miss, generates a safe column name and registers the column.
    /// On cache hit, returns the existing id (incrementing perf counters accordingly).
    ColumnId get_or_create_test_column(
        std::uint32_t test_num,
        std::string_view test_text,
        std::uint32_t result_index,
        CellValue::Type value_type = CellValue::Double
    );

    /// Get or create a column id for a fixed (identity/meta) column.
    ColumnId get_or_create_fixed_column(
        std::string_view name,
        CellValue::Type type = CellValue::String
    );

    /// Look up metadata for a column id.
    const ColumnMeta& meta(ColumnId id) const;

    /// All registered columns in order of registration.
    const std::vector<ColumnMeta>& columns_in_order() const { return columns_; }

    /// Number of registered columns.
    std::size_t size() const { return columns_.size(); }

    /// Look up a column id by name.  Returns -1 if not found.
    ColumnId find_by_name(std::string_view name) const;

private:
    /// Generate a safe column name for a test column (delegates to the
    /// same logic as the old test_column_name()).
    static std::string make_test_column_name(
        std::uint32_t test_num,
        std::string_view test_text,
        std::uint32_t result_index
    );

    /// Handle duplicate column names by appending a stable suffix.
    std::string resolve_name_conflict(std::string desired);

    std::vector<ColumnMeta> columns_;
    std::unordered_map<std::string, ColumnId> name_to_id_;
    std::unordered_map<TestColumnKey, ColumnId, TestColumnKeyHash> test_key_to_id_;
};

}  // namespace deepyield_stdf
