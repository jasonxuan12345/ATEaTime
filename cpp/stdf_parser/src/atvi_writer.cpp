// Direct ATVI generation writer core (plan WP3).  Arrow/Parquet construction
// lives in atvi_arrow.cpp; the frozen outcome classifier in
// atvi_classifier.cpp.
#include "deepyield_stdf/atvi_writer.hpp"

#include <arrow/io/api.h>
#include <arrow/ipc/writer.h>
#include <arrow/table.h>
#include <parquet/file_reader.h>
#include <parquet/arrow/writer.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <sstream>

namespace deepyield_stdf {
namespace {

constexpr const char* BUILDER_NAME = "cpp-direct-atvi-v1";
constexpr const char* OUTCOME_ENGINE = "device-test-outcomes-v1";
constexpr const char* FINAL_DEVICE_SCOPE = "final-device-scope-v1";
constexpr const char* CORRELATION_ENGINE = "correlation-engine-v1";
constexpr std::int64_t ROW_GROUP_TARGET_ROWS = 65536;

std::string hex16(const std::array<std::uint8_t, 16>& bytes) {
  static const char* hex = "0123456789abcdef";
  std::string out;
  out.reserve(32);
  for (auto byte : bytes) {
    out.push_back(hex[(byte >> 4) & 0x0f]);
    out.push_back(hex[byte & 0x0f]);
  }
  return out;
}

std::string json_escape(const std::string& value) {
  std::ostringstream out;
  out << '"';
  for (char ch : value) {
    switch (ch) {
      case '"': out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (static_cast<unsigned char>(ch) < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
              << static_cast<int>(ch) << std::dec;
        } else {
          out << ch;
        }
    }
  }
  out << '"';
  return out.str();
}

std::string float_to_json(double value) {
  if (!std::isfinite(value)) return "null";
  if (std::floor(value) == value && std::abs(value) < 1e15) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(1) << value;
    return out.str();
  }
  std::ostringstream out;
  out << std::setprecision(17) << value;
  return out.str();
}

std::string canonical_json_array(
    const std::array<std::uint8_t, 16>& observation_id,
    const std::optional<double>& result_value,
    const std::string& value_state,
    const std::string& test_outcome,
    const std::string& outcome_evidence) {
  std::ostringstream out;
  out << "[\"" << hex16(observation_id) << "\",";
  out << (result_value.has_value() ? float_to_json(*result_value) : "null");
  out << "," << json_escape(value_state) << "," << json_escape(test_outcome)
      << "," << json_escape(outcome_evidence) << "]";
  return out.str();
}

std::string compact_canonical_json_array(const CompactAtviRow& row) {
  static const char* value_states[] = {"FINITE", "NULL", "NON_FINITE", "NON_NUMERIC"};
  static const char* outcomes[] = {"PASS", "FAIL", "UNKNOWN"};
  static const char* evidence[] = {"EXPLICIT", "INFERRED_LIMIT", "UNAVAILABLE"};
  const bool numeric = row.value_state == 0 || row.value_state == 2;
  std::ostringstream out;
  out << "[\"" << hex16(row.observation_id) << "\","
      << (numeric ? float_to_json(row.result_value) : std::string("null")) << ","
      << json_escape(value_states[row.value_state % 4]) << ","
      << json_escape(outcomes[row.test_outcome % 3]) << ","
      << json_escape(evidence[row.outcome_evidence % 3]) << "]";
  return out.str();
}

std::string row_group_ipc_sha256(const std::shared_ptr<arrow::RecordBatch>& batch) {
  auto sink_result = arrow::io::BufferOutputStream::Create();
  if (!sink_result.ok()) throw std::runtime_error(sink_result.status().ToString());
  auto sink = sink_result.ValueOrDie();
  auto normalized = batch->ReplaceSchemaMetadata(nullptr);
  auto writer_result = arrow::ipc::MakeStreamWriter(sink, normalized->schema());
  if (!writer_result.ok()) throw std::runtime_error(writer_result.status().ToString());
  auto writer = writer_result.ValueOrDie();
  PARQUET_THROW_NOT_OK(writer->WriteRecordBatch(*normalized));
  PARQUET_THROW_NOT_OK(writer->Close());
  auto buffer_result = sink->Finish();
  if (!buffer_result.ok()) throw std::runtime_error(buffer_result.status().ToString());
  const auto buffer = buffer_result.ValueOrDie();
  Sha256 hasher;
  hasher.update(buffer->data(), static_cast<std::size_t>(buffer->size()));
  return hasher.hex_digest_current();
}

struct BucketSortKey {
  std::array<std::uint8_t, 16> key{};
  std::string lot;
  std::string wafer;
  std::string site;
  std::int64_t part_index = 0;
  std::int32_t attempt_index = 0;
  std::optional<std::int64_t> source_record_sequence;
  std::optional<std::int32_t> result_index;
  std::array<std::uint8_t, 16> observation_id{};
};

bool operator<(const BucketSortKey& left, const BucketSortKey& right) {
  if (left.key != right.key) return left.key < right.key;
  if (left.lot != right.lot) return left.lot < right.lot;
  if (left.wafer != right.wafer) return left.wafer < right.wafer;
  if (left.site != right.site) return left.site < right.site;
  if (left.part_index != right.part_index) return left.part_index < right.part_index;
  if (left.attempt_index != right.attempt_index) return left.attempt_index < right.attempt_index;
  if (left.source_record_sequence != right.source_record_sequence) {
    if (!left.source_record_sequence.has_value()) return false;
    if (!right.source_record_sequence.has_value()) return true;
    return *left.source_record_sequence < *right.source_record_sequence;
  }
  if (left.result_index != right.result_index) {
    if (!left.result_index.has_value()) return false;
    if (!right.result_index.has_value()) return true;
    return *left.result_index < *right.result_index;
  }
  return left.observation_id < right.observation_id;
}

BucketSortKey sort_key_of(const AtviObservation& row) {
  return BucketSortKey{
      row.test_item_key,
      row.lot_id.value_or(""),
      row.wafer_id.value_or(""),
      row.site.value_or(""),
      row.part_index,
      row.attempt_index,
      row.source_record_sequence,
      row.result_index,
      row.observation_id,
  };
}

std::string read_file_bytes(const fs::path& path) {
  std::ifstream in(path, std::ios::binary);
  return std::string((std::istreambuf_iterator<char>(in)),
                     std::istreambuf_iterator<char>());
}

}  // namespace

AtviWriter::AtviWriter(fs::path generation_dir, std::uint32_t bucket_count)
    : generation_dir_(std::move(generation_dir)), bucket_count_(bucket_count) {
  if (bucket_count_ == 0) bucket_count_ = 1;
  spill_dir_ = generation_dir_ / "spill_runs";
}

void AtviWriter::add_observation(const AtviObservation& observation) {
  if (finished_) {
    throw std::logic_error("cannot add an observation after ATVI finish()");
  }
  if (observation.record_type != "PTR" && observation.record_type != "MPR") {
    throw std::invalid_argument("ATVI observation record_type must be PTR or MPR");
  }
  auto temp_it = temp_cache_.find(observation.test_item_key);
  const std::uint8_t record_type = observation.record_type == "MPR" ? 1 : 0;
  const std::int32_t result_index = observation.result_index.value_or(-1);
  if (temp_it == temp_cache_.end()) {
    const std::string payload = test_item_payload(
        observation.record_type, observation.test_num,
        observation.result_index.has_value() ? std::to_string(*observation.result_index) : "-");
    if (observation.test_item_key != test_item_key_raw(payload)) {
      throw std::invalid_argument("ATVI observation test_item_key does not match identity payload");
    }
    IdentityCacheEntry entry;
    entry.record_type = record_type;
    entry.test_num = observation.test_num;
    entry.result_index = result_index;
    entry.temp_bucket = static_cast<std::uint32_t>(
        bucket_hash_prefix(payload) % temp_partition_count_);
    temp_it = temp_cache_.emplace(observation.test_item_key, entry).first;
  } else if (temp_it->second.record_type != record_type ||
             temp_it->second.test_num != observation.test_num ||
             temp_it->second.result_index != result_index) {
    throw std::invalid_argument("ATVI observation test_item_key does not match identity payload");
  }
  if (!observation_ids_.insert(observation.observation_id).second) {
    throw std::invalid_argument("duplicate ATVI observation_id");
  }
  locator_[observation.test_item_key];
  CompactAtviRow row = to_compact_row(observation, &intern_);
  const std::uint32_t temp = temp_it->second.temp_bucket;
  auto& buffer = spill_buffers_[temp];
  buffer.push_back(std::move(row));
  ++row_count_;
  if (buffer.size() >= spill_buffer_limit_) spill_to_run(temp);
}

void AtviWriter::set_bucket_count(std::uint32_t bucket_count) {
  if (finished_) {
    throw std::logic_error("cannot change bucket count after ATVI finish()");
  }
  if (bucket_count == 0) bucket_count = 1;
  bucket_count_ = bucket_count;
}

void AtviWriter::set_spill_buffer_limit(std::size_t limit) {
  if (finished_) {
    throw std::logic_error("cannot change spill buffer limit after ATVI finish()");
  }
  spill_buffer_limit_ = limit > 0 ? limit : 1;
}

void AtviWriter::spill_to_run(std::uint32_t temp_bucket) {
  auto it = spill_buffers_.find(temp_bucket);
  if (it == spill_buffers_.end() || it->second.empty()) return;
  const auto spill_t0 = std::chrono::steady_clock::now();
  sort_compact_rows(&it->second, intern_);
  const auto sort_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                           std::chrono::steady_clock::now() - spill_t0)
                           .count();
  fs::create_directories(spill_dir_);
  const std::uint32_t run = spill_run_counter_++;
  std::ostringstream name;
  name << "bucket_" << std::setw(3) << std::setfill('0') << temp_bucket << "_run_"
       << std::setw(3) << std::setfill('0') << run << ".bin";
  const auto path = spill_dir_ / name.str();
  write_spill_run(path, temp_bucket, run, it->second);
  std::cerr << "[atvi] spill bucket=" << temp_bucket << " rows=" << it->second.size()
            << " sort_ms=" << sort_ms << "\n";
  spill_runs_[temp_bucket].push_back(path);
  it->second.clear();
}

void AtviWriter::add_attempt(const AtviAttempt& attempt) {
  if (finished_) {
    throw std::logic_error("cannot add an attempt after ATVI finish()");
  }
  attempts_.push_back(attempt);
}

void AtviWriter::add_mapping_evidence(const AtviMappingEvidence& evidence) {
  if (finished_) {
    throw std::logic_error("cannot add mapping evidence after ATVI finish()");
  }
  mapping_evidence_.push_back(evidence);
}

std::string AtviWriter::finish() {
  if (finished_) return finished_fingerprint_;
  for (auto& entry : spill_buffers_) spill_to_run(entry.first);
  std::map<std::uint32_t, std::vector<fs::path>> runs_by_final;
  for (const auto& pair : spill_runs_) {
    auto& target = runs_by_final[pair.first % bucket_count_];
    target.insert(target.end(), pair.second.begin(), pair.second.end());
  }
  std::cerr << "[atvi] rows=" << row_count_ << " temp_runs=" << spill_run_counter_
            << " final_buckets=" << runs_by_final.size() << "\n";
  fs::create_directories(generation_dir_ / "buckets");
  const char* sidecar_env = std::getenv("DEEPYIELD_L2_SIDECAR_ROW_GROUPS");
  const bool write_sidecar = sidecar_env != nullptr && std::string(sidecar_env) == "1";
  std::ofstream sidecar_groups;
  std::uint64_t sidecar_group_count = 0;
  if (write_sidecar) {
    fs::create_directories(generation_dir_ / "l2_sidecar");
    sidecar_groups.open(generation_dir_ / "l2_sidecar" / "row_groups.jsonl",
                        std::ios::binary);
    if (!sidecar_groups) throw std::runtime_error("cannot open L2 sidecar row groups");
  }
  std::array<std::uint8_t, 16> current_item_key{};
  bool has_item = false;
  Sha256 item_content_sha;
  for (const auto& pair : runs_by_final) {
    std::uint32_t group_counter = 0;
    const std::uint32_t final_bucket = pair.first;
    const auto& runs = pair.second;
    std::ostringstream name;
    name << "bucket_" << std::setw(3) << std::setfill('0') << final_bucket << ".parquet";
    const auto path = generation_dir_ / "buckets" / name.str();
    PARQUET_ASSIGN_OR_THROW(
        auto outfile, arrow::io::FileOutputStream::Open(path.string()));
    PARQUET_ASSIGN_OR_THROW(
        auto writer,
        parquet::arrow::FileWriter::Open(
            *atvi_bucket_schema(), arrow::default_memory_pool(), outfile,
            parquet::WriterProperties::Builder().compression(arrow::Compression::ZSTD)
                ->compression_level(1)
                ->disable_dictionary()
                ->build()));
    merge_spill_runs(
        runs, intern_, ROW_GROUP_TARGET_ROWS,
        [&](std::vector<CompactAtviRow>& chunk) {
          const auto& key = chunk.front().test_item_key;
          if (!has_item || key != current_item_key) {
            if (has_item) {
              locator_[current_item_key].content_sha256 = item_content_sha.hex_digest_current();
            }
            current_item_key = key;
            has_item = true;
            item_content_sha.reset();
          }
          auto& item = locator_[key];
          if (item.row_group_count == 0) item.first_row_group = group_counter;
          item.row_group_count += 1;
          item.bucket_id = final_bucket;
          std::shared_ptr<arrow::RecordBatch> batch;
          if (!append_observation_table_compact(chunk, intern_, source_file_hash_, &batch).ok()) {
            throw std::runtime_error("failed to build ATVI bucket batch (compact)");
          }
          auto table_result = arrow::Table::FromRecordBatches({batch});
          if (!table_result.ok()) {
            throw std::runtime_error("failed to convert ATVI record batch to table");
          }
          PARQUET_THROW_NOT_OK(
              writer->WriteTable(*table_result.ValueOrDie(), batch->num_rows()));
          std::optional<std::int64_t> group_min_sequence;
          std::optional<std::int64_t> group_max_sequence;
          for (const auto& row : chunk) {
            const std::string canonical_row = compact_canonical_json_array(row);
            item_content_sha.update(canonical_row.data(), canonical_row.size());
            item_content_sha.update("\n", 1);
            item.row_count += 1;
            if (row.source_record_sequence >= 0) {
              if (!group_min_sequence || row.source_record_sequence < *group_min_sequence) {
                group_min_sequence = row.source_record_sequence;
              }
              if (!group_max_sequence || row.source_record_sequence > *group_max_sequence) {
                group_max_sequence = row.source_record_sequence;
              }
            }
            if (row.value_state == 0) {
              item.finite_count += 1;
              if (!item.min.has_value() || row.result_value < *item.min) {
                item.min = row.result_value;
              }
              if (!item.max.has_value() || row.result_value > *item.max) {
                item.max = row.result_value;
              }
            }
            if (row.unit_id != UINT32_MAX) {
              const std::string& unit = intern_.at(row.unit_id);
              if (std::find(item.units.begin(), item.units.end(), unit) == item.units.end()) {
                item.units.push_back(unit);
              }
            }
          }
          if (write_sidecar) {
            sidecar_groups << "{\"test_item_key\":\"" << hex16(key)
                           << "\",\"bucket_id\":" << final_bucket
                           << ",\"row_group_index\":" << group_counter
                           << ",\"physical_row_count\":" << batch->num_rows()
                           << ",\"min_source_record_sequence\":"
                           << (group_min_sequence.has_value()
                                   ? std::to_string(*group_min_sequence)
                                   : std::string("null"))
                           << ",\"max_source_record_sequence\":"
                           << (group_max_sequence.has_value()
                                   ? std::to_string(*group_max_sequence)
                                   : std::string("null"))
                           << ",\"source_row_group_sha256\":\""
                           << row_group_ipc_sha256(batch) << "\"}\n";
            ++sidecar_group_count;
          }
          group_counter += 1;
        });
    PARQUET_THROW_NOT_OK(writer->Close());
  }
  if (has_item) locator_[current_item_key].content_sha256 = item_content_sha.hex_digest_current();
  if (!spill_dir_.empty()) fs::remove_all(spill_dir_);
  write_locator();
  if (!write_attempt_facts_parquet(generation_dir_ / "attempt_facts.parquet", attempts_).ok()) {
    throw std::runtime_error("failed to write attempt facts");
  }
  if (!write_mapping_evidence_parquet(generation_dir_ / "mapping_evidence.parquet",
                                      mapping_evidence_)
           .ok()) {
    throw std::runtime_error("failed to write mapping evidence");
  }
  const std::string fingerprint = compute_fingerprint();
  if (write_sidecar) {
    sidecar_groups.close();
    // The ATVI manifest checksums the inventory below, so the files must be
    // complete on disk before write_manifest() runs.
    std::vector<std::array<std::uint8_t, 16>> item_keys;
    item_keys.reserve(temp_cache_.size());
    for (const auto& entry : temp_cache_) item_keys.push_back(entry.first);
    std::sort(item_keys.begin(), item_keys.end());
    std::ofstream sidecar_items(generation_dir_ / "l2_sidecar" / "items.jsonl",
                                std::ios::binary);
    if (!sidecar_items) throw std::runtime_error("cannot open L2 sidecar items");
    for (const auto& key : item_keys) {
      const auto& identity = temp_cache_.at(key);
      sidecar_items << "{\"test_item_key\":\"" << hex16(key)
                    << "\",\"record_type\":\""
                    << (identity.record_type == 1 ? "MPR" : "PTR")
                    << "\",\"test_num\":" << identity.test_num
                    << ",\"result_index\":"
                    << (identity.result_index >= 0
                            ? std::to_string(identity.result_index)
                            : std::string("null"))
                    << "}\n";
    }
    sidecar_items.close();
  }
  write_manifest(fingerprint);
  if (write_sidecar) {
    std::ofstream sidecar_manifest(generation_dir_ / "l2_sidecar" / "manifest.json",
                                   std::ios::binary);
    sidecar_manifest << "{\"schema_version\":\"l2-aggregate-sidecar-v1\","
                        "\"status\":\"PARTIAL\",\"builder_version\":"
                        "\"l2-atvi-fused-aggregate-v1\",\"reason\":"
                        "\"aggregate/candidate products not yet complete\","
                        "\"inventory_version\":\"atvi-row-group-inventory-v1\","
                        "\"row_group_count\":" << sidecar_group_count << "}";
  }
  finished_ = true;
  finished_fingerprint_ = fingerprint;
  return fingerprint;
}

std::string AtviWriter::compute_fingerprint() const {
  std::string project_type = "UNKNOWN";
  if (!attempts_.empty()) project_type = attempts_[0].project_type;
  std::ostringstream out;
  out << "{";
  out << "\"builder\":" << json_escape(BUILDER_NAME) << ",";
  out << "\"bucket_count\":" << bucket_count_ << ",";
  out << "\"contract\":" << json_escape("test-item-index-v1") << ",";
  out << "\"coverage_profile\":" << json_escape("COMPLETE_NATIVE") << ",";
  out << "\"final_device_scope\":" << json_escape(FINAL_DEVICE_SCOPE) << ",";
  out << "\"outcome_engine\":" << json_escape(OUTCOME_ENGINE) << ",";
  out << "\"project_type\":" << json_escape(project_type) << ",";
  out << "\"source_file_hash\":" << json_escape(source_file_hash_) << "}";
  return Sha256::hex_digest(out.str());
}

void AtviWriter::write_manifest(const std::string& fingerprint) const {
  std::ostringstream tables;
  tables << "[";
  bool first = true;
  for (const auto& entry : fs::recursive_directory_iterator(generation_dir_)) {
    if (!entry.is_regular_file()) continue;
    const bool is_parquet = entry.path().extension() == ".parquet";
    // Sidecar inventory files are checksummed by the same manifest so L2 can
    // trust the row-group hashes without re-reading the buckets.
    const bool is_sidecar_inventory =
        entry.path().extension() == ".jsonl" &&
        entry.path().parent_path().filename() == "l2_sidecar";
    if (!is_parquet && !is_sidecar_inventory) continue;
    if (!first) tables << ",";
    first = false;
    const auto rel = fs::relative(entry.path(), generation_dir_).generic_string();
    const auto bytes = read_file_bytes(entry.path());
    std::int64_t row_count = -1;
    if (is_parquet) {
      try {
        auto reader = parquet::ParquetFileReader::OpenFile(entry.path().string());
        row_count = reader->metadata()->num_rows();
      } catch (...) {
        row_count = -1;
      }
    }
    tables << "{\"path\":" << json_escape(rel)
           << ",\"sha256\":" << json_escape(Sha256::hex_digest(bytes))
           << ",\"size\":" << bytes.size()
           << ",\"row_count\":" << row_count << "}";
  }
  tables << "]";
  std::ofstream manifest(generation_dir_ / "manifest.json");
  manifest << "{\"status\":\"COMPLETE\",\"builder\":" << json_escape(BUILDER_NAME)
           << ",\"fingerprint\":" << json_escape(fingerprint)
           << ",\"source_file_hash\":" << json_escape(source_file_hash_)
           << ",\"schema_versions\":{\"bucket_v1\":"
           << json_escape(schema_canonical_sha256(*atvi_bucket_schema()))
           << ",\"atvi_locator\":" << json_escape(schema_canonical_sha256(*atvi_locator_schema()))
           << ",\"attempt_facts\":"
           << json_escape(schema_canonical_sha256(*atvi_attempt_facts_schema()))
           << ",\"mapping_evidence\":"
           << json_escape(schema_canonical_sha256(*atvi_mapping_evidence_schema()))
           << "},\"tables\":" << tables.str() << "}";
}

void AtviWriter::write_locator() const {
  if (!write_locator_parquet(generation_dir_ / "locator.parquet", locator_).ok()) {
    throw std::runtime_error("failed to write ATVI locator");
  }
}

}  // namespace deepyield_stdf
