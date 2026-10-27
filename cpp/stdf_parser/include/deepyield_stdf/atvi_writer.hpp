// Direct ATVI generation writer (plan WP3).
//
// Writes the same contract as the Python reference builder
// (deepyield/io/test_item_index/builder.py): item-aligned bucket parquet
// files, locator, attempt facts (AAF), mapping evidence, and a COMPLETE
// manifest.  Digest algorithms (test_item_key / bucket_hash / observation_id
// / content_sha256) are shared with sha256.hpp.
#pragma once

#include "deepyield_stdf/sha256.hpp"

#include <array>
#include <arrow/api.h>
#include <cstring>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;

namespace deepyield_stdf {

}  // namespace deepyield_stdf

namespace std {

template <>
struct hash<std::array<std::uint8_t, 16>> {
  std::size_t operator()(const std::array<std::uint8_t, 16>& value) const noexcept {
    std::size_t result = 1469598103934665603ULL;
    for (std::uint8_t byte : value) {
      result ^= byte;
      result *= 1099511628211ULL;
    }
    return result;
  }
};

}  // namespace std

namespace deepyield_stdf {

// One PTR/MPR observation (bucket row grain), mirroring contract_v1.json
// bucket_v1 fields.  observation_id/test_item_key are 16-byte digests.
struct AtviObservation {
  std::array<std::uint8_t, 16> observation_id{};
  std::string observation_source;  // NATIVE_RECORD / WIDE_ALL_BACKFILL
  std::string source_file_hash;
  std::int64_t part_index = 0;
  std::int32_t attempt_index = 0;
  std::optional<std::int64_t> source_record_sequence;
  std::string record_type;  // PTR / MPR
  std::uint32_t test_num = 0;
  std::optional<std::int32_t> result_index;
  std::array<std::uint8_t, 16> test_item_key{};
  std::optional<std::string> column_name;
  std::optional<std::string> test_name;
  std::optional<std::string> unit;
  std::optional<double> lsl;
  std::optional<double> usl;
  std::optional<double> result_value;
  std::string value_state;  // FINITE / NULL / NON_FINITE / NON_NUMERIC
  std::string test_outcome;   // PASS / FAIL / UNKNOWN
  std::string outcome_evidence;  // EXPLICIT / INFERRED_LIMIT / UNAVAILABLE
  std::optional<std::uint8_t> test_flag;
  std::optional<std::uint8_t> param_flag;
  std::optional<std::string> lot_id;
  std::optional<std::string> wafer_id;
  std::optional<std::string> site;
  std::optional<std::int32_t> die_x;
  std::optional<std::int32_t> die_y;
  std::optional<std::string> part_txt;
  std::optional<std::string> source_lot_id;
  std::optional<std::string> source_wafer_id;
  std::optional<std::int32_t> source_die_x;
  std::optional<std::int32_t> source_die_y;
  std::optional<std::string> test_stage;
  std::optional<std::string> temperature;
  std::optional<std::string> program_version;
  std::optional<std::int64_t> test_start_time_us;  // UTC epoch microseconds
  std::optional<std::int64_t> test_end_time_us;
};

// Asset Attempt Facts row (attempt_facts.parquet grain).
struct AtviAttempt {
  std::string source_file_hash;
  std::string source_record_id;
  std::int64_t part_index = 0;
  std::int32_t attempt_index = 0;
  std::optional<std::int64_t> source_record_sequence;
  std::string project_type;  // EWS / FT / UNKNOWN
  std::optional<std::string> test_stage;
  std::optional<std::string> chip_uid;
  std::optional<std::string> lot_id;
  std::optional<std::string> wafer_id;
  std::optional<std::string> site;
  std::optional<std::int32_t> die_x;
  std::optional<std::int32_t> die_y;
  std::optional<std::string> part_txt;
  std::optional<std::int32_t> hbin;
  std::optional<std::int32_t> sbin;
  std::optional<std::int64_t> test_start_time_us;
  std::optional<std::int64_t> test_end_time_us;
  std::optional<std::string> temperature;
  std::optional<std::string> program_version;
  std::string temperature_source;
  std::string program_version_source;
  std::optional<std::string> ft_lot_ref;
  std::optional<std::string> source_lot_id;
  std::optional<std::string> source_wafer_id;
  std::optional<std::int32_t> source_die_x;
  std::optional<std::int32_t> source_die_y;
  std::optional<std::string> attempt_id;
  std::optional<std::int64_t> attempt_sequence;
  std::optional<std::string> duplicate_key;
  std::string device_result;  // PASS / FAIL / ABORT / UNKNOWN
  std::string identity_resolution;
  std::vector<std::string> identity_reason_codes;
  std::string source_evidence_version;
  std::string mapping_candidate_capture_state;
};

// Mapping evidence row (mapping_evidence.parquet grain).
struct AtviMappingEvidence {
  std::string source_file_hash;
  std::string source_record_id;
  std::int64_t part_index = 0;
  std::int32_t attempt_index = 0;
  std::string evidence_kind;   // IDENTITY / MAPPING
  std::uint32_t candidate_index = 0;
  std::string evidence_origin;  // IDENTITY_VALUES / MAPPING_CANDIDATE / SOURCE_TUPLE_FALLBACK
  std::string lot_tag;
  std::string wafer_tag;
  std::string die_x_tag;
  std::string die_y_tag;
  std::optional<std::string> lot_payload;
  std::optional<std::string> wafer_payload;
  std::optional<std::string> die_x_payload;
  std::optional<std::string> die_y_payload;
  std::string policy_input_version;
};

struct AtviLocatorEntry {
  std::uint32_t bucket_id = 0;
  std::uint32_t first_row_group = 0;
  std::uint32_t row_group_count = 0;
  std::int64_t row_count = 0;
  std::int64_t finite_count = 0;
  std::optional<double> min;
  std::optional<double> max;
  std::vector<std::string> units;
  std::string content_sha256;
};

// Compact fixed-width intermediate row (plan WP3 spill design): strings are
// dictionary ids into a per-asset intern table, nullable fields use sentinels.
struct CompactAtviRow {
  std::array<std::uint8_t, 16> observation_id{};
  std::array<std::uint8_t, 16> test_item_key{};
  std::int32_t part_index = 0;
  std::int32_t attempt_index = 0;
  std::int64_t source_record_sequence = -1;  // -1 = null
  std::uint8_t record_type = 0;              // 0 PTR, 1 MPR
  std::uint32_t test_num = 0;
  std::int32_t result_index = -1;            // -1 = null
  std::uint32_t column_name_id = UINT32_MAX;  // UINT32_MAX = null
  std::uint32_t test_name_id = UINT32_MAX;
  std::uint32_t unit_id = UINT32_MAX;
  double lsl = 0.0;
  double usl = 0.0;
  bool has_lsl = false;
  bool has_usl = false;
  double result_value = 0.0;
  std::uint8_t value_state = 1;    // 0 FINITE, 1 NULL, 2 NON_FINITE, 3 NON_NUMERIC
  std::uint8_t test_outcome = 2;   // 0 PASS, 1 FAIL, 2 UNKNOWN
  std::uint8_t outcome_evidence = 2;  // 0 EXPLICIT, 1 INFERRED_LIMIT, 2 UNAVAILABLE
  std::uint8_t test_flag = 255;    // 255 = null
  std::uint8_t param_flag = 255;
  std::uint32_t lot_id = UINT32_MAX;
  std::uint32_t wafer_id = UINT32_MAX;
  std::uint32_t site_id = UINT32_MAX;
  std::int32_t die_x = INT32_MIN;  // sentinel = null
  std::int32_t die_y = INT32_MIN;
  std::uint32_t part_txt_id = UINT32_MAX;
  std::uint32_t test_stage_id = UINT32_MAX;
  std::uint32_t temperature_id = UINT32_MAX;
  std::uint32_t program_version_id = UINT32_MAX;
  std::int64_t test_start_time_us = -1;  // -1 = null
  std::int64_t test_end_time_us = -1;
};

// Per-asset string intern table (dictionary ids).
class StringIntern {
 public:
  // Empty strings map to UINT32_MAX (null), matching the Python reference.
  std::uint32_t intern(const std::string& value);
  const std::string& at(std::uint32_t id) const { return values_.at(id); }
  std::size_t size() const { return values_.size(); }

 private:
  std::vector<std::string> values_;
  std::unordered_map<std::string, std::uint32_t> index_;
};

class AtviWriter {
 public:
  AtviWriter(fs::path generation_dir, std::uint32_t bucket_count);

  // Add one observation; identity and duplicate observation ids are rejected
  // before the row enters a spill buffer.
  void add_observation(const AtviObservation& observation);
  void add_attempt(const AtviAttempt& attempt);
  void add_mapping_evidence(const AtviMappingEvidence& evidence);

  // Write buckets/locator/AAF/mapping-evidence/manifest.  Returns manifest
  // fingerprint (computed from canonical sorted-key components).
  std::string finish();

  // Adaptive bucket count (contract_v1.json): must be called before finish().
  void set_bucket_count(std::uint32_t bucket_count);
  void set_spill_buffer_limit(std::size_t limit);
  void set_source_file_hash(const std::string& source_file_hash) {
    if (finished_) {
      throw std::logic_error("cannot change source hash after ATVI finish()");
    }
    source_file_hash_ = source_file_hash;
  }
  std::size_t observation_key_count() const { return locator_.size(); }
  std::size_t total_row_count() const { return row_count_; }

 private:
  struct IdentityCacheEntry {
    std::uint8_t record_type = 0;
    std::uint32_t test_num = 0;
    std::int32_t result_index = -1;
    std::uint32_t temp_bucket = 0;
  };
  void flush_item(const std::array<std::uint8_t, 16>& key,
                  std::vector<AtviObservation>& rows, std::uint32_t bucket_id,
                  std::uint32_t& group_counter);
  std::string compute_fingerprint() const;
  void write_manifest(const std::string& fingerprint) const;
  void write_locator() const;
  void spill_to_run(std::uint32_t temp_bucket);
  StringIntern intern_;
  std::size_t spill_buffer_limit_ = 65536;
  std::uint32_t temp_partition_count_ = 128;
  std::uint32_t spill_run_counter_ = 0;
  std::size_t row_count_ = 0;
  bool finished_ = false;
  std::string finished_fingerprint_;
  std::string source_file_hash_;
  fs::path spill_dir_;
  std::unordered_map<std::array<std::uint8_t, 16>, IdentityCacheEntry> temp_cache_;
  std::map<std::uint32_t, std::vector<CompactAtviRow>> spill_buffers_;
  std::map<std::uint32_t, std::vector<fs::path>> spill_runs_;
  std::vector<AtviAttempt> attempts_;
  std::vector<AtviMappingEvidence> mapping_evidence_;
  std::unordered_set<std::array<std::uint8_t, 16>> observation_ids_;
  std::map<std::array<std::uint8_t, 16>, AtviLocatorEntry> locator_;
  fs::path generation_dir_;
  std::uint32_t bucket_count_ = 8;
};

// Arrow helpers (atvi_arrow.cpp) used by the writer and by parity tests.
std::shared_ptr<arrow::Schema> atvi_bucket_schema();
std::shared_ptr<arrow::Schema> atvi_locator_schema();
std::shared_ptr<arrow::Schema> atvi_attempt_facts_schema();
std::shared_ptr<arrow::Schema> atvi_mapping_evidence_schema();

arrow::Status append_observation_table(
    const std::vector<AtviObservation>& rows,
    std::shared_ptr<arrow::RecordBatch>* batch);

arrow::Status write_attempt_facts_parquet(
    const fs::path& path, const std::vector<AtviAttempt>& attempts);

arrow::Status write_mapping_evidence_parquet(
    const fs::path& path, const std::vector<AtviMappingEvidence>& evidence);

arrow::Status write_locator_parquet(
    const fs::path& path,
    const std::map<std::array<std::uint8_t, 16>, AtviLocatorEntry>& locator);

std::string schema_canonical_sha256(const arrow::Schema& schema);

// Convert one observation to the compact row (dictionary ids).
CompactAtviRow to_compact_row(const AtviObservation& observation, StringIntern* intern);

// Write one chunk of compact rows as an ATVI bucket record batch.
arrow::Status append_observation_table_compact(
    const std::vector<CompactAtviRow>& rows,
    const StringIntern& intern,
    const std::string& source_file_hash,
    std::shared_ptr<arrow::RecordBatch>* batch);

void sort_compact_rows(std::vector<CompactAtviRow>* rows, const StringIntern& intern);

void write_spill_run(const fs::path& path, std::uint32_t bucket_id,
                     std::uint32_t run_index,
                     const std::vector<CompactAtviRow>& rows);

void merge_spill_runs(
    const std::vector<fs::path>& runs, const StringIntern& intern,
    std::size_t chunk_rows,
    const std::function<void(std::vector<CompactAtviRow>&)>& sink);

// Canonical observation id matching
// deepyield.io.test_item_index.identity.observation_id_native.
inline std::array<std::uint8_t, 16> observation_id_native(
    const std::string& source_file_hash,
    std::int64_t part_index,
    std::int32_t attempt_index,
    std::int64_t source_record_sequence,
    const std::string& record_type,
    std::uint32_t test_num,
    std::optional<std::int32_t> result_index) {
  std::ostringstream out;
  out << "[\"native-observation-v1\",\"" << source_file_hash << "\"," << part_index << ","
      << attempt_index << "," << source_record_sequence << ",\"" << record_type << "\","
      << test_num << ","
      << (result_index.has_value() ? std::to_string(*result_index) : "null") << "]";
  auto digest = Sha256::raw_digest(out.str());
  std::array<std::uint8_t, 16> result{};
  std::memcpy(result.data(), digest.data(), 16);
  return result;
}

// Pure TEST-OUTCOME-001 classifier (mirrors test_item_outcome_classifier.py).
struct OutcomeClassification {
  std::string test_outcome;      // PASS / FAIL / UNKNOWN
  std::string outcome_evidence;  // EXPLICIT / INFERRED_LIMIT / UNAVAILABLE
};

OutcomeClassification classify_test_item_outcome(
    std::optional<std::uint8_t> test_flag,
    std::optional<std::uint8_t> param_flag,
    std::optional<double> result_value,
    std::optional<double> low_limit,
    std::optional<double> high_limit,
    bool catalog_conflict,
    bool missing_catalog,
    bool missing_device);

std::string value_state_of(std::optional<double> value);

}  // namespace deepyield_stdf
