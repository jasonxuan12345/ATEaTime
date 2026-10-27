/// ColumnRegistry implementation.
/// Phase 2 of the DeepYield STDF parser performance optimization plan.

#include "deepyield_stdf/column_registry.hpp"
#include "deepyield_stdf/perf_counters.hpp"

#include <algorithm>
#include <cctype>
#include <set>
#include <sstream>

namespace deepyield_stdf {

namespace {

// ---- safe_column_name (same logic as main.cpp) ----

std::string safe_column_name(std::string_view value) {
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

}  // namespace

// ---- ColumnRegistry ----

std::string ColumnRegistry::make_test_column_name(
    std::uint32_t test_num,
    std::string_view test_text,
    std::uint32_t result_index)
{
    ++g_perf().test_column_name_calls;
    std::string base = "TEST_" + std::to_string(test_num);
    const std::string safe = safe_column_name(test_text);
    if (safe != "UNKNOWN") {
        base = std::to_string(test_num) + "_" + safe;
    }
    if (result_index > 0) {
        base += "_" + std::to_string(result_index);
    }
    return base;
}

std::string ColumnRegistry::resolve_name_conflict(std::string desired) {
    // If the name is already taken, append _N suffix
    if (name_to_id_.find(desired) == name_to_id_.end()) {
        return desired;
    }
    for (int suffix = 2; suffix < 10000; ++suffix) {
        std::string candidate = desired + "_" + std::to_string(suffix);
        if (name_to_id_.find(candidate) == name_to_id_.end()) {
            return candidate;
        }
    }
    // Extremely unlikely: fallback to unique id-based name
    return desired + "_" + std::to_string(columns_.size());
}

ColumnId ColumnRegistry::get_or_create_test_column(
    std::uint32_t test_num,
    std::string_view test_text,
    std::uint32_t result_index,
    CellValue::Type value_type)
{
    TestColumnKey key{test_num, result_index};

    // Check cache
    auto it = test_key_to_id_.find(key);
    if (it != test_key_to_id_.end()) {
        ++g_perf().test_column_cache_hits;
        return it->second;
    }

    ++g_perf().test_column_cache_misses;

    // Generate name
    std::string name = make_test_column_name(test_num, test_text, result_index);
    name = resolve_name_conflict(name);

    // Register
    ColumnId id = static_cast<ColumnId>(columns_.size());
    ColumnMeta meta;
    meta.id = id;
    meta.name = name;
    meta.type = value_type;
    meta.is_test = true;
    meta.test_num = test_num;
    meta.test_text = std::string(test_text);
    meta.result_index = result_index;

    columns_.push_back(meta);
    name_to_id_[name] = id;
    test_key_to_id_[key] = id;

    return id;
}

ColumnId ColumnRegistry::get_or_create_fixed_column(
    std::string_view name,
    CellValue::Type type)
{
    std::string name_str(name);
    auto it = name_to_id_.find(name_str);
    if (it != name_to_id_.end()) {
        return it->second;
    }

    ColumnId id = static_cast<ColumnId>(columns_.size());
    ColumnMeta meta;
    meta.id = id;
    meta.name = name_str;
    meta.type = type;
    meta.is_identity = true;

    columns_.push_back(meta);
    name_to_id_[name_str] = id;

    return id;
}

const ColumnMeta& ColumnRegistry::meta(ColumnId id) const {
    return columns_.at(static_cast<std::size_t>(id));
}

ColumnId ColumnRegistry::find_by_name(std::string_view name) const {
    auto it = name_to_id_.find(std::string(name));
    if (it != name_to_id_.end()) {
        return it->second;
    }
    return static_cast<ColumnId>(-1);
}

}  // namespace deepyield_stdf
