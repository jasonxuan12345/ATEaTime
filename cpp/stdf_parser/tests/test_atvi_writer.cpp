// C++ direct ATVI writer smoke test + parity fixture generator.
//
// Usage: test_atvi_writer [output_dir]
// Writes a complete ATVI generation for the same EWS fixture used by the
// Python reference builder (tests/test_io/test_atvi_builder.py) so that the
// Python parity test can compare bucket/locator/AAF rows byte-for-byte.
#include "deepyield_stdf/atvi_writer.hpp"

#include <arrow/api.h>
#include <parquet/file_reader.h>

#include <cstring>
#include <chrono>
#include <iostream>
#include <string>

namespace {

using deepyield_stdf::AtviAttempt;
using deepyield_stdf::AtviObservation;
using deepyield_stdf::AtviWriter;
using deepyield_stdf::classify_test_item_outcome;
using deepyield_stdf::observation_id_native;
using deepyield_stdf::test_item_key_raw;
using deepyield_stdf::test_item_payload;
using deepyield_stdf::value_state_of;

const char* SOURCE_HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

std::array<std::uint8_t, 16> key_of(const std::string& record_type, std::uint32_t test_num,
                                    std::optional<std::int32_t> result_index) {
  return test_item_key_raw(test_item_payload(
      record_type, test_num, result_index.has_value() ? std::to_string(*result_index) : "-"));
}

AtviObservation observation(
    std::int64_t part_index, std::int32_t attempt_index, std::int64_t sequence,
    const std::string& record_type, std::uint32_t test_num,
    std::optional<std::int32_t> result_index, const std::string& column_name,
    const std::string& test_name, const std::string& unit, double lsl, double usl,
    double result, std::optional<std::uint8_t> test_flag, const std::string& lot,
    const std::string& wafer, const std::string& site, std::int32_t die_x,
    std::int32_t die_y) {
  AtviObservation row;
  row.observation_id = observation_id_native(SOURCE_HASH, part_index, attempt_index, sequence,
                                              record_type, test_num, result_index);
  row.observation_source = "NATIVE_RECORD";
  row.source_file_hash = SOURCE_HASH;
  row.part_index = part_index;
  row.attempt_index = attempt_index;
  row.source_record_sequence = sequence;
  row.record_type = record_type;
  row.test_num = test_num;
  row.result_index = result_index;
  row.test_item_key = key_of(record_type, test_num, result_index);
  row.column_name = column_name;
  row.test_name = test_name;
  row.unit = unit;
  row.lsl = lsl;
  row.usl = usl;
  row.result_value = result;
  row.value_state = value_state_of(row.result_value);
  auto classification = classify_test_item_outcome(test_flag, 0, row.result_value, row.lsl,
                                                   row.usl, false, false, false);
  row.test_outcome = classification.test_outcome;
  row.outcome_evidence = classification.outcome_evidence;
  row.test_flag = test_flag;
  row.param_flag = 0;
  row.lot_id = lot;
  row.wafer_id = wafer;
  row.site = site;
  row.die_x = die_x;
  row.die_y = die_y;
  row.test_stage = "EWS";
  return row;
}

AtviAttempt attempt(std::int64_t part_index, std::int32_t attempt_index,
                    std::int64_t sequence, const std::string& lot, const std::string& wafer,
                    std::int32_t die_x, std::int32_t die_y, std::int32_t hbin,
                    std::int32_t sbin) {
  AtviAttempt row;
  row.source_file_hash = SOURCE_HASH;
  row.source_record_id =
      std::string(SOURCE_HASH) + ":" + std::to_string(sequence);
  row.part_index = part_index;
  row.attempt_index = attempt_index;
  row.source_record_sequence = sequence;
  row.project_type = "EWS";
  row.test_stage = "EWS";
  row.chip_uid = "EWS|" + lot + "|" + wafer + "|" + std::to_string(die_x) + "|" +
                 std::to_string(die_y);
  row.lot_id = lot;
  row.wafer_id = wafer;
  row.site = "1";
  row.die_x = die_x;
  row.die_y = die_y;
  row.hbin = hbin;
  row.sbin = sbin;
  row.temperature_source = "UNAVAILABLE";
  row.program_version_source = "UNAVAILABLE";
  row.device_result = hbin == 1 ? "PASS" : "FAIL";
  row.identity_resolution = "RESOLVED";
  row.source_evidence_version = "cpp-direct-v1";
  row.mapping_candidate_capture_state = "UNAVAILABLE_LEGACY";
  return row;
}

int failures = 0;
void check(bool condition, const std::string& label) {
  if (!condition) {
    std::cerr << "FAIL: " << label << "\n";
    ++failures;
  }
}

}  // namespace

int main(int argc, char** argv) {
  using deepyield_stdf::AtviWriter;
  if (argc > 2 && std::string(argv[1]) == "--bench") {
    const std::size_t count = static_cast<std::size_t>(std::stoull(argv[2]));
    AtviWriter bench(fs::temp_directory_path() / "atvi-bench", 8);
    bench.set_source_file_hash(SOURCE_HASH);
    const auto started = std::chrono::steady_clock::now();
    for (std::size_t i = 0; i < count; ++i) {
      auto row = observation(static_cast<std::int64_t>(i % 1000), 0,
                             static_cast<std::int64_t>(i), "PTR", 10, std::nullopt,
                             "10_VDD", "VDD", "V", 0.0, 2.0, 1.5, 0, "L1", "W1", "1",
                             static_cast<std::int32_t>(i % 100), static_cast<std::int32_t>(i % 100));
      bench.add_observation(row);
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                             std::chrono::steady_clock::now() - started)
                             .count();
    std::cout << "bench add_observation count=" << count << " ms=" << elapsed << "\n";
    return 0;
  }
  const fs::path out_dir =
      argc > 1 ? fs::path(argv[1]) : fs::temp_directory_path() / "atvi-smoke";
  fs::remove_all(out_dir);

  AtviWriter writer(out_dir, 8);
  writer.set_source_file_hash(SOURCE_HASH);
  // Force more than one sorted run per temporary partition so this smoke
  // fixture exercises the bounded spill + k-way merge path.
  writer.set_spill_buffer_limit(1);
  // part 1 / attempt 0
  writer.add_observation(observation(1, 0, 1, "PTR", 10, std::nullopt, "10_VDD", "VDD", "V",
                                     0.0, 2.0, 1.5, 0, "L1", "W1", "1", 0, 0));
  writer.add_observation(observation(1, 0, 2, "MPR", 11, 0, "11_CUR_0", "CUR_0", "A", 0.0,
                                     5.0, 1.0, 0, "L1", "W1", "1", 0, 0));
  writer.add_observation(observation(1, 0, 3, "MPR", 11, 1, "11_CUR_1", "CUR_1", "A", 0.0,
                                     5.0, 2.5, 0, "L1", "W1", "1", 0, 0));
  // part 2 / attempt 0
  writer.add_observation(observation(2, 0, 4, "PTR", 10, std::nullopt, "10_VDD", "VDD", "V",
                                     0.0, 2.0, 3.0, 128, "L1", "W1", "1", 1, 1));
  writer.add_observation(observation(2, 0, 5, "MPR", 11, 0, "11_CUR_0", "CUR_0", "A", 0.0,
                                     5.0, 2.0, 0, "L1", "W1", "1", 1, 1));
  writer.add_observation(observation(2, 0, 6, "MPR", 11, 1, "11_CUR_1", "CUR_1", "A", 0.0,
                                     5.0, 6.0, 0, "L1", "W1", "1", 1, 1));
  {
    auto mismatched = observation(3, 0, 7, "PTR", 12, std::nullopt, "12_OTHER",
                                  "OTHER", "V", 0.0, 2.0, 1.0, 0, "L1", "W1", "1", 2, 2);
    mismatched.test_item_key = key_of("PTR", 10, std::nullopt);
    bool rejected = false;
    try {
      writer.add_observation(mismatched);
    } catch (const std::invalid_argument&) {
      rejected = true;
    }
    check(rejected, "cached test item key rejects mismatched identity");
  }
  writer.add_attempt(attempt(1, 0, 1, "L1", "W1", 0, 0, 1, 1));
  writer.add_attempt(attempt(2, 0, 2, "L1", "W1", 1, 1, 5, 10));
  const std::string fingerprint = writer.finish();
  check(writer.finish() == fingerprint, "finish is idempotent");

  check(!fingerprint.empty(), "fingerprint non-empty");
  check(fs::exists(out_dir / "manifest.json"), "manifest exists");
  check(fs::exists(out_dir / "locator.parquet"), "locator exists");
  check(fs::exists(out_dir / "attempt_facts.parquet"), "attempt facts exist");
  check(fs::exists(out_dir / "mapping_evidence.parquet"), "mapping evidence exists");
  check(!fs::exists(out_dir / "spill_runs"), "spill runs cleaned up");

  // Locator must contain exactly the three logical items.
  auto locator_file = parquet::ParquetFileReader::OpenFile(
      (out_dir / "locator.parquet").string());
  check(locator_file->metadata()->num_rows() == 3, "locator has 3 items");

  std::cout << "atvi fixture written to " << out_dir.string() << "\n";
  if (failures == 0) {
    std::cout << "test_atvi_writer: OK\n";
    return 0;
  }
  std::cerr << "test_atvi_writer: " << failures << " failure(s)\n";
  return 1;
}
