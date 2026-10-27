// Compact row projection, binary spill runs, and k-way merge (plan WP3
// bounded spill/merge).
#include "deepyield_stdf/atvi_writer.hpp"

#include <algorithm>
#include <cstring>
#include <fstream>
#include <queue>
#include <stdexcept>
#include <string_view>

namespace deepyield_stdf {
namespace {

constexpr char RUN_MAGIC[8] = {'A', 'T', 'V', '1', 'R', 'U', 'N', '1'};

struct RunHeader {
  char magic[8];
  std::uint32_t bucket_id;
  std::uint32_t run_index;
  std::uint64_t record_count;
  std::uint32_t record_bytes;
};

std::uint32_t string_id(const std::optional<std::string>& value, StringIntern* intern) {
  if (!value.has_value() || value->empty()) return UINT32_MAX;
  return intern->intern(*value);
}

}  // namespace

std::uint32_t StringIntern::intern(const std::string& value) {
  auto it = index_.find(value);
  if (it != index_.end()) return it->second;
  const std::uint32_t id = static_cast<std::uint32_t>(values_.size());
  values_.push_back(value);
  index_.emplace(value, id);
  return id;
}

CompactAtviRow to_compact_row(const AtviObservation& observation, StringIntern* intern) {
  CompactAtviRow row;
  row.observation_id = observation.observation_id;
  row.test_item_key = observation.test_item_key;
  row.part_index = static_cast<std::int32_t>(observation.part_index);
  row.attempt_index = observation.attempt_index;
  row.source_record_sequence = observation.source_record_sequence.value_or(-1);
  row.record_type = observation.record_type == "MPR" ? 1 : 0;
  row.test_num = observation.test_num;
  row.result_index = observation.result_index.value_or(-1);
  row.column_name_id = string_id(observation.column_name, intern);
  row.test_name_id = string_id(observation.test_name, intern);
  row.unit_id = string_id(observation.unit, intern);
  row.has_lsl = observation.lsl.has_value();
  row.lsl = observation.lsl.value_or(0.0);
  row.has_usl = observation.usl.has_value();
  row.usl = observation.usl.value_or(0.0);
  row.result_value = observation.result_value.value_or(0.0);
  if (observation.value_state == "FINITE") row.value_state = 0;
  else if (observation.value_state == "NON_FINITE") row.value_state = 2;
  else if (observation.value_state == "NON_NUMERIC") row.value_state = 3;
  else row.value_state = 1;
  if (observation.test_outcome == "PASS") row.test_outcome = 0;
  else if (observation.test_outcome == "FAIL") row.test_outcome = 1;
  else row.test_outcome = 2;
  if (observation.outcome_evidence == "EXPLICIT") row.outcome_evidence = 0;
  else if (observation.outcome_evidence == "INFERRED_LIMIT") row.outcome_evidence = 1;
  else row.outcome_evidence = 2;
  row.test_flag = observation.test_flag.value_or(255);
  row.param_flag = observation.param_flag.value_or(255);
  row.lot_id = string_id(observation.lot_id, intern);
  row.wafer_id = string_id(observation.wafer_id, intern);
  row.site_id = string_id(observation.site, intern);
  row.die_x = observation.die_x.value_or(INT32_MIN);
  row.die_y = observation.die_y.value_or(INT32_MIN);
  row.part_txt_id = string_id(observation.part_txt, intern);
  row.test_stage_id = string_id(observation.test_stage, intern);
  row.temperature_id = string_id(observation.temperature, intern);
  row.program_version_id = string_id(observation.program_version, intern);
  row.test_start_time_us = observation.test_start_time_us.value_or(-1);
  row.test_end_time_us = observation.test_end_time_us.value_or(-1);
  return row;
}

namespace {

bool compact_less(const CompactAtviRow& left, const CompactAtviRow& right,
                  const StringIntern& intern) {
  if (left.test_item_key != right.test_item_key) return left.test_item_key < right.test_item_key;
  const std::string_view ll = left.lot_id == UINT32_MAX ? std::string_view()
                                                       : std::string_view(intern.at(left.lot_id));
  const std::string_view rl = right.lot_id == UINT32_MAX ? std::string_view()
                                                         : std::string_view(intern.at(right.lot_id));
  if (ll != rl) return ll < rl;
  const std::string_view lw = left.wafer_id == UINT32_MAX ? std::string_view()
                                                          : std::string_view(intern.at(left.wafer_id));
  const std::string_view rw = right.wafer_id == UINT32_MAX ? std::string_view()
                                                           : std::string_view(intern.at(right.wafer_id));
  if (lw != rw) return lw < rw;
  const std::string_view ls = left.site_id == UINT32_MAX ? std::string_view()
                                                         : std::string_view(intern.at(left.site_id));
  const std::string_view rs = right.site_id == UINT32_MAX ? std::string_view()
                                                          : std::string_view(intern.at(right.site_id));
  if (ls != rs) return ls < rs;
  if (left.part_index != right.part_index) return left.part_index < right.part_index;
  if (left.attempt_index != right.attempt_index) return left.attempt_index < right.attempt_index;
  if (left.source_record_sequence != right.source_record_sequence) {
    if (left.source_record_sequence < 0) return false;
    if (right.source_record_sequence < 0) return true;
    return left.source_record_sequence < right.source_record_sequence;
  }
  if (left.result_index != right.result_index) {
    if (left.result_index < 0) return false;
    if (right.result_index < 0) return true;
    return left.result_index < right.result_index;
  }
  return left.observation_id < right.observation_id;
}

}  // namespace

void sort_compact_rows(std::vector<CompactAtviRow>* rows, const StringIntern& intern) {
  std::sort(rows->begin(), rows->end(),
            [&intern](const CompactAtviRow& left, const CompactAtviRow& right) {
              return compact_less(left, right, intern);
            });
}

void write_spill_run(const fs::path& path, std::uint32_t bucket_id, std::uint32_t run_index,
                     const std::vector<CompactAtviRow>& rows) {
  RunHeader header;
  std::memcpy(header.magic, RUN_MAGIC, 8);
  header.bucket_id = bucket_id;
  header.run_index = run_index;
  header.record_count = rows.size();
  header.record_bytes = static_cast<std::uint32_t>(sizeof(CompactAtviRow));
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  if (!out) throw std::runtime_error("unable to create spill run: " + path.string());
  out.write(reinterpret_cast<const char*>(&header), sizeof(header));
  for (const auto& row : rows) {
    out.write(reinterpret_cast<const char*>(&row), sizeof(row));
  }
  if (!out) throw std::runtime_error("unable to write spill run: " + path.string());
}

void merge_spill_runs(
    const std::vector<fs::path>& runs, const StringIntern& intern,
    std::size_t chunk_rows,
    const std::function<void(std::vector<CompactAtviRow>&)>& sink) {
  struct Stream {
    std::ifstream in;
    CompactAtviRow current;
    std::uint64_t remaining = 0;
    bool valid = false;
    std::size_t stream_index = 0;
  };
  std::vector<Stream> streams;
  streams.reserve(runs.size());
  for (const auto& path : runs) {
    Stream stream;
    stream.in.open(path, std::ios::binary);
    RunHeader header;
    stream.in.read(reinterpret_cast<char*>(&header), sizeof(header));
    if (!stream.in || std::memcmp(header.magic, RUN_MAGIC, sizeof(RUN_MAGIC)) != 0 ||
        header.record_count == 0 || header.record_bytes != sizeof(CompactAtviRow)) {
      throw std::runtime_error("invalid spill run: " + path.string());
    }
    std::error_code size_error;
    const auto file_size = fs::file_size(path, size_error);
    const auto expected_size = sizeof(RunHeader) +
                               header.record_count * sizeof(CompactAtviRow);
    if (size_error || file_size != expected_size) {
      throw std::runtime_error("truncated or malformed spill run: " + path.string());
    }
    stream.remaining = header.record_count;
    stream.in.read(reinterpret_cast<char*>(&stream.current), sizeof(CompactAtviRow));
    stream.valid = stream.in.good() && stream.remaining > 0;
    if (stream.valid) --stream.remaining;
    stream.stream_index = streams.size();
    streams.push_back(std::move(stream));
  }
  auto compare_streams = [&intern](const Stream* left, const Stream* right) {
    if (!right->valid) return true;
    if (!left->valid) return false;
    return compact_less(right->current, left->current, intern);
  };
  std::priority_queue<Stream*, std::vector<Stream*>, decltype(compare_streams)> heap(
      compare_streams);
  for (auto& stream : streams) {
    if (stream.valid) heap.push(&stream);
  }
  std::vector<CompactAtviRow> chunk;
  chunk.reserve(chunk_rows);
  auto flush = [&]() {
    if (!chunk.empty()) {
      sink(chunk);
      chunk.clear();
    }
  };
  while (!heap.empty()) {
    Stream* top = heap.top();
    heap.pop();
    if (!chunk.empty() &&
        (chunk.front().test_item_key != top->current.test_item_key ||
         chunk.size() >= chunk_rows)) {
      flush();
    }
    chunk.push_back(top->current);
    if (top->remaining > 0) {
      top->in.read(reinterpret_cast<char*>(&top->current), sizeof(CompactAtviRow));
      top->valid = top->in.good();
      if (top->valid) --top->remaining;
    } else {
      top->valid = false;
    }
    if (top->valid) heap.push(top);
  }
  flush();
}

}  // namespace deepyield_stdf
