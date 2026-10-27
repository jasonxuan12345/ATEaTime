// Arrow/Parquet construction for the direct ATVI writer (plan WP3).
#include "deepyield_stdf/atvi_writer.hpp"

#include <arrow/array.h>
#include <arrow/io/api.h>
#include <arrow/record_batch.h>
#include <arrow/table.h>
#include <parquet/properties.h>
#include <parquet/arrow/writer.h>

#include <sstream>

namespace deepyield_stdf {
namespace {

std::string canonical_field_type(const std::shared_ptr<arrow::DataType>& type) {
  return type->ToString();
}

std::string canonical_schema_text(const arrow::Schema& schema) {
  std::ostringstream out;
  out << "[";
  for (int i = 0; i < schema.num_fields(); ++i) {
    if (i) out << ",";
    const auto& field = schema.field(i);
    out << "{\"name\":\"" << field->name() << "\",\"type\":\""
        << canonical_field_type(field->type()) << "\",\"nullable\":"
        << (field->nullable() ? "true" : "false") << "}";
  }
  out << "]";
  return out.str();
}

template <typename Builder, typename T>
void append_optional(Builder* builder, const std::optional<T>& value) {
  if (value.has_value()) {
    builder->Append(*value);
  } else {
    builder->AppendNull();
  }
}

template <typename Builder>
void append_optional_string(Builder* builder, const std::optional<std::string>& value) {
  if (value.has_value()) {
    builder->Append(*value);
  } else {
    builder->AppendNull();
  }
}

arrow::Status finish_array(arrow::ArrayBuilder* builder, std::shared_ptr<arrow::Array>* out) {
  return builder->Finish(out);
}

}  // namespace

std::shared_ptr<arrow::Schema> atvi_bucket_schema() {
  return arrow::schema({
      arrow::field("observation_id", arrow::fixed_size_binary(16), false),
      arrow::field("observation_source", arrow::utf8(), false),
      arrow::field("source_file_hash", arrow::utf8(), false),
      arrow::field("part_index", arrow::int64(), false),
      arrow::field("attempt_index", arrow::int32(), false),
      arrow::field("source_record_sequence", arrow::int64(), true),
      arrow::field("record_type", arrow::utf8(), false),
      arrow::field("test_num", arrow::uint32(), false),
      arrow::field("result_index", arrow::int32(), true),
      arrow::field("test_item_key", arrow::fixed_size_binary(16), false),
      arrow::field("column_name", arrow::utf8(), true),
      arrow::field("test_name", arrow::utf8(), true),
      arrow::field("unit", arrow::utf8(), true),
      arrow::field("lsl", arrow::float64(), true),
      arrow::field("usl", arrow::float64(), true),
      arrow::field("result_value", arrow::float64(), true),
      arrow::field("value_state", arrow::utf8(), false),
      arrow::field("test_outcome", arrow::utf8(), false),
      arrow::field("outcome_evidence", arrow::utf8(), false),
      arrow::field("test_flag", arrow::uint8(), true),
      arrow::field("param_flag", arrow::uint8(), true),
      arrow::field("lot_id", arrow::utf8(), true),
      arrow::field("wafer_id", arrow::utf8(), true),
      arrow::field("site", arrow::utf8(), true),
      arrow::field("die_x", arrow::int32(), true),
      arrow::field("die_y", arrow::int32(), true),
      arrow::field("part_txt", arrow::utf8(), true),
      arrow::field("source_lot_id", arrow::utf8(), true),
      arrow::field("source_wafer_id", arrow::utf8(), true),
      arrow::field("source_die_x", arrow::int32(), true),
      arrow::field("source_die_y", arrow::int32(), true),
      arrow::field("test_stage", arrow::utf8(), true),
      arrow::field("temperature", arrow::utf8(), true),
      arrow::field("program_version", arrow::utf8(), true),
      arrow::field("test_start_time", arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"), true),
      arrow::field("test_end_time", arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"), true),
  });
}

std::shared_ptr<arrow::Schema> atvi_locator_schema() {
  return arrow::schema({
      arrow::field("test_item_key", arrow::fixed_size_binary(16), false),
      arrow::field("bucket_id", arrow::uint16(), false),
      arrow::field("first_row_group_id", arrow::uint32(), false),
      arrow::field("row_group_count", arrow::uint32(), false),
      arrow::field("row_count", arrow::int64(), false),
      arrow::field("finite_count", arrow::int64(), false),
      arrow::field("min", arrow::float64(), true),
      arrow::field("max", arrow::float64(), true),
      arrow::field("units", arrow::list(arrow::utf8()), true),
      arrow::field("content_sha256", arrow::utf8(), false),
  });
}

std::shared_ptr<arrow::Schema> atvi_attempt_facts_schema() {
  return arrow::schema({
      arrow::field("source_file_hash", arrow::utf8(), false),
      arrow::field("source_record_id", arrow::utf8(), false),
      arrow::field("part_index", arrow::int64(), false),
      arrow::field("attempt_index", arrow::int32(), false),
      arrow::field("source_record_sequence", arrow::int64(), true),
      arrow::field("project_type", arrow::utf8(), false),
      arrow::field("test_stage", arrow::utf8(), true),
      arrow::field("chip_uid", arrow::utf8(), true),
      arrow::field("lot_id", arrow::utf8(), true),
      arrow::field("wafer_id", arrow::utf8(), true),
      arrow::field("site", arrow::utf8(), true),
      arrow::field("die_x", arrow::int32(), true),
      arrow::field("die_y", arrow::int32(), true),
      arrow::field("part_txt", arrow::utf8(), true),
      arrow::field("hbin", arrow::int32(), true),
      arrow::field("sbin", arrow::int32(), true),
      arrow::field("test_start_time", arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"), true),
      arrow::field("test_end_time", arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"), true),
      arrow::field("temperature", arrow::utf8(), true),
      arrow::field("program_version", arrow::utf8(), true),
      arrow::field("temperature_source", arrow::utf8(), false),
      arrow::field("program_version_source", arrow::utf8(), false),
      arrow::field("ft_lot_ref", arrow::utf8(), true),
      arrow::field("source_lot_id", arrow::utf8(), true),
      arrow::field("source_wafer_id", arrow::utf8(), true),
      arrow::field("source_die_x", arrow::int32(), true),
      arrow::field("source_die_y", arrow::int32(), true),
      arrow::field("attempt_id", arrow::utf8(), true),
      arrow::field("attempt_sequence", arrow::int64(), true),
      arrow::field("duplicate_key", arrow::utf8(), true),
      arrow::field("device_result", arrow::utf8(), false),
      arrow::field("identity_resolution", arrow::utf8(), false),
      arrow::field("identity_reason_codes", arrow::list(arrow::utf8()), false),
      arrow::field("source_evidence_version", arrow::utf8(), false),
      arrow::field("mapping_candidate_capture_state", arrow::utf8(), false),
      arrow::field("mapping_evidence_product_version", arrow::utf8(), true),
      arrow::field("mapping_evidence_product_sha256", arrow::utf8(), true),
  });
}

std::shared_ptr<arrow::Schema> atvi_mapping_evidence_schema() {
  return arrow::schema({
      arrow::field("source_file_hash", arrow::utf8(), false),
      arrow::field("source_record_id", arrow::utf8(), false),
      arrow::field("part_index", arrow::int64(), false),
      arrow::field("attempt_index", arrow::int32(), false),
      arrow::field("evidence_kind", arrow::utf8(), false),
      arrow::field("candidate_index", arrow::uint32(), false),
      arrow::field("evidence_origin", arrow::utf8(), false),
      arrow::field("lot_tag", arrow::utf8(), false),
      arrow::field("wafer_tag", arrow::utf8(), false),
      arrow::field("die_x_tag", arrow::utf8(), false),
      arrow::field("die_y_tag", arrow::utf8(), false),
      arrow::field("lot_payload", arrow::utf8(), true),
      arrow::field("wafer_payload", arrow::utf8(), true),
      arrow::field("die_x_payload", arrow::utf8(), true),
      arrow::field("die_y_payload", arrow::utf8(), true),
      arrow::field("policy_input_version", arrow::utf8(), false),
  });
}

std::string schema_canonical_sha256(const arrow::Schema& schema) {
  return Sha256::hex_digest(canonical_schema_text(schema));
}

arrow::Status append_observation_table(
    const std::vector<AtviObservation>& rows,
    std::shared_ptr<arrow::RecordBatch>* batch) {
  arrow::FixedSizeBinaryBuilder observation_id(arrow::fixed_size_binary(16));
  arrow::StringBuilder observation_source;
  arrow::StringBuilder source_file_hash;
  arrow::Int64Builder part_index;
  arrow::Int32Builder attempt_index;
  arrow::Int64Builder source_record_sequence;
  arrow::StringBuilder record_type;
  arrow::UInt32Builder test_num;
  arrow::Int32Builder result_index;
  arrow::FixedSizeBinaryBuilder test_item_key(arrow::fixed_size_binary(16));
  arrow::StringBuilder column_name;
  arrow::StringBuilder test_name;
  arrow::StringBuilder unit;
  arrow::DoubleBuilder lsl;
  arrow::DoubleBuilder usl;
  arrow::DoubleBuilder result_value;
  arrow::StringBuilder value_state;
  arrow::StringBuilder test_outcome;
  arrow::StringBuilder outcome_evidence;
  arrow::UInt8Builder test_flag;
  arrow::UInt8Builder param_flag;
  arrow::StringBuilder lot_id;
  arrow::StringBuilder wafer_id;
  arrow::StringBuilder site;
  arrow::Int32Builder die_x;
  arrow::Int32Builder die_y;
  arrow::StringBuilder part_txt;
  arrow::StringBuilder source_lot_id;
  arrow::StringBuilder source_wafer_id;
  arrow::Int32Builder source_die_x;
  arrow::Int32Builder source_die_y;
  arrow::StringBuilder test_stage;
  arrow::StringBuilder temperature;
  arrow::StringBuilder program_version;
  arrow::TimestampBuilder test_start_time(arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"),
                                          arrow::default_memory_pool());
  arrow::TimestampBuilder test_end_time(arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"),
                                        arrow::default_memory_pool());

  for (const auto& row : rows) {
    ARROW_RETURN_NOT_OK(observation_id.Append(row.observation_id.data()));
    ARROW_RETURN_NOT_OK(observation_source.Append(row.observation_source));
    ARROW_RETURN_NOT_OK(source_file_hash.Append(row.source_file_hash));
    ARROW_RETURN_NOT_OK(part_index.Append(row.part_index));
    ARROW_RETURN_NOT_OK(attempt_index.Append(row.attempt_index));
    append_optional(&source_record_sequence, row.source_record_sequence);
    ARROW_RETURN_NOT_OK(record_type.Append(row.record_type));
    ARROW_RETURN_NOT_OK(test_num.Append(row.test_num));
    append_optional(&result_index, row.result_index);
    ARROW_RETURN_NOT_OK(test_item_key.Append(row.test_item_key.data()));
    append_optional_string(&column_name, row.column_name);
    append_optional_string(&test_name, row.test_name);
    append_optional_string(&unit, row.unit);
    append_optional(&lsl, row.lsl);
    append_optional(&usl, row.usl);
    append_optional(&result_value, row.result_value);
    ARROW_RETURN_NOT_OK(value_state.Append(row.value_state));
    ARROW_RETURN_NOT_OK(test_outcome.Append(row.test_outcome));
    ARROW_RETURN_NOT_OK(outcome_evidence.Append(row.outcome_evidence));
    append_optional(&test_flag, row.test_flag);
    append_optional(&param_flag, row.param_flag);
    append_optional_string(&lot_id, row.lot_id);
    append_optional_string(&wafer_id, row.wafer_id);
    append_optional_string(&site, row.site);
    append_optional(&die_x, row.die_x);
    append_optional(&die_y, row.die_y);
    append_optional_string(&part_txt, row.part_txt);
    append_optional_string(&source_lot_id, row.source_lot_id);
    append_optional_string(&source_wafer_id, row.source_wafer_id);
    append_optional(&source_die_x, row.source_die_x);
    append_optional(&source_die_y, row.source_die_y);
    append_optional_string(&test_stage, row.test_stage);
    append_optional_string(&temperature, row.temperature);
    append_optional_string(&program_version, row.program_version);
    append_optional(&test_start_time, row.test_start_time_us);
    append_optional(&test_end_time, row.test_end_time_us);
  }
  std::vector<std::shared_ptr<arrow::Array>> arrays;
  ARROW_RETURN_NOT_OK(observation_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(observation_source.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_file_hash.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(part_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(attempt_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_record_sequence.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(record_type.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_num.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(result_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_item_key.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(column_name.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_name.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(unit.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(lsl.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(usl.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(result_value.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(value_state.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_outcome.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(outcome_evidence.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_flag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(param_flag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(lot_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(wafer_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(site.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_x.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_y.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(part_txt.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_lot_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_wafer_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_die_x.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_die_y.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_stage.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(temperature.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(program_version.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_start_time.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_end_time.Finish(&arrays.emplace_back()));
  *batch = arrow::RecordBatch::Make(atvi_bucket_schema(),
                                    static_cast<std::int64_t>(rows.size()), arrays);
  return arrow::Status::OK();
}

namespace {

const char* VALUE_STATE_STRINGS[] = {"FINITE", "NULL", "NON_FINITE", "NON_NUMERIC"};
const char* OUTCOME_STRINGS[] = {"PASS", "FAIL", "UNKNOWN"};
const char* EVIDENCE_STRINGS[] = {"EXPLICIT", "INFERRED_LIMIT", "UNAVAILABLE"};

arrow::Status append_dict_string(arrow::StringBuilder* builder, std::uint32_t id,
                                 const StringIntern& intern) {
  if (id == UINT32_MAX) return builder->AppendNull();
  return builder->Append(intern.at(id));
}

}  // namespace

arrow::Status append_observation_table_compact(
    const std::vector<CompactAtviRow>& rows, const StringIntern& intern,
    const std::string& source_file_hash, std::shared_ptr<arrow::RecordBatch>* batch) {
  arrow::FixedSizeBinaryBuilder observation_id(arrow::fixed_size_binary(16));
  arrow::StringBuilder observation_source;
  arrow::StringBuilder source_file_hash_builder;
  arrow::Int64Builder part_index;
  arrow::Int32Builder attempt_index;
  arrow::Int64Builder source_record_sequence;
  arrow::StringBuilder record_type;
  arrow::UInt32Builder test_num;
  arrow::Int32Builder result_index;
  arrow::FixedSizeBinaryBuilder test_item_key(arrow::fixed_size_binary(16));
  arrow::StringBuilder column_name;
  arrow::StringBuilder test_name;
  arrow::StringBuilder unit;
  arrow::DoubleBuilder lsl;
  arrow::DoubleBuilder usl;
  arrow::DoubleBuilder result_value;
  arrow::StringBuilder value_state;
  arrow::StringBuilder test_outcome;
  arrow::StringBuilder outcome_evidence;
  arrow::UInt8Builder test_flag;
  arrow::UInt8Builder param_flag;
  arrow::StringBuilder lot_id;
  arrow::StringBuilder wafer_id;
  arrow::StringBuilder site;
  arrow::Int32Builder die_x;
  arrow::Int32Builder die_y;
  arrow::StringBuilder part_txt;
  arrow::StringBuilder source_lot_id;
  arrow::StringBuilder source_wafer_id;
  arrow::Int32Builder source_die_x;
  arrow::Int32Builder source_die_y;
  arrow::StringBuilder test_stage;
  arrow::StringBuilder temperature;
  arrow::StringBuilder program_version;
  arrow::TimestampBuilder test_start_time(arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"),
                                          arrow::default_memory_pool());
  arrow::TimestampBuilder test_end_time(arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"),
                                        arrow::default_memory_pool());

  for (const auto& row : rows) {
    ARROW_RETURN_NOT_OK(observation_id.Append(row.observation_id.data()));
    ARROW_RETURN_NOT_OK(observation_source.Append("NATIVE_RECORD"));
    ARROW_RETURN_NOT_OK(source_file_hash_builder.Append(source_file_hash));
    ARROW_RETURN_NOT_OK(part_index.Append(row.part_index));
    ARROW_RETURN_NOT_OK(attempt_index.Append(row.attempt_index));
    if (row.source_record_sequence < 0) {
      ARROW_RETURN_NOT_OK(source_record_sequence.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(source_record_sequence.Append(row.source_record_sequence));
    }
    ARROW_RETURN_NOT_OK(record_type.Append(row.record_type == 1 ? "MPR" : "PTR"));
    ARROW_RETURN_NOT_OK(test_num.Append(row.test_num));
    if (row.result_index < 0) {
      ARROW_RETURN_NOT_OK(result_index.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(result_index.Append(row.result_index));
    }
    ARROW_RETURN_NOT_OK(test_item_key.Append(row.test_item_key.data()));
    ARROW_RETURN_NOT_OK(append_dict_string(&column_name, row.column_name_id, intern));
    ARROW_RETURN_NOT_OK(append_dict_string(&test_name, row.test_name_id, intern));
    ARROW_RETURN_NOT_OK(append_dict_string(&unit, row.unit_id, intern));
    if (row.has_lsl) {
      ARROW_RETURN_NOT_OK(lsl.Append(row.lsl));
    } else {
      ARROW_RETURN_NOT_OK(lsl.AppendNull());
    }
    if (row.has_usl) {
      ARROW_RETURN_NOT_OK(usl.Append(row.usl));
    } else {
      ARROW_RETURN_NOT_OK(usl.AppendNull());
    }
    if (row.value_state == 1 || row.value_state == 3) {
      ARROW_RETURN_NOT_OK(result_value.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(result_value.Append(row.result_value));
    }
    ARROW_RETURN_NOT_OK(value_state.Append(VALUE_STATE_STRINGS[row.value_state % 4]));
    ARROW_RETURN_NOT_OK(test_outcome.Append(OUTCOME_STRINGS[row.test_outcome % 3]));
    ARROW_RETURN_NOT_OK(outcome_evidence.Append(EVIDENCE_STRINGS[row.outcome_evidence % 3]));
    if (row.test_flag == 255) {
      ARROW_RETURN_NOT_OK(test_flag.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(test_flag.Append(row.test_flag));
    }
    if (row.param_flag == 255) {
      ARROW_RETURN_NOT_OK(param_flag.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(param_flag.Append(row.param_flag));
    }
    ARROW_RETURN_NOT_OK(append_dict_string(&lot_id, row.lot_id, intern));
    ARROW_RETURN_NOT_OK(append_dict_string(&wafer_id, row.wafer_id, intern));
    ARROW_RETURN_NOT_OK(append_dict_string(&site, row.site_id, intern));
    if (row.die_x == INT32_MIN) {
      ARROW_RETURN_NOT_OK(die_x.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(die_x.Append(row.die_x));
    }
    if (row.die_y == INT32_MIN) {
      ARROW_RETURN_NOT_OK(die_y.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(die_y.Append(row.die_y));
    }
    ARROW_RETURN_NOT_OK(append_dict_string(&part_txt, row.part_txt_id, intern));
    ARROW_RETURN_NOT_OK(source_lot_id.AppendNull());
    ARROW_RETURN_NOT_OK(source_wafer_id.AppendNull());
    ARROW_RETURN_NOT_OK(source_die_x.AppendNull());
    ARROW_RETURN_NOT_OK(source_die_y.AppendNull());
    ARROW_RETURN_NOT_OK(append_dict_string(&test_stage, row.test_stage_id, intern));
    ARROW_RETURN_NOT_OK(append_dict_string(&temperature, row.temperature_id, intern));
    ARROW_RETURN_NOT_OK(append_dict_string(&program_version, row.program_version_id, intern));
    if (row.test_start_time_us < 0) {
      ARROW_RETURN_NOT_OK(test_start_time.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(test_start_time.Append(row.test_start_time_us));
    }
    if (row.test_end_time_us < 0) {
      ARROW_RETURN_NOT_OK(test_end_time.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(test_end_time.Append(row.test_end_time_us));
    }
  }
  std::vector<std::shared_ptr<arrow::Array>> arrays;
  ARROW_RETURN_NOT_OK(observation_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(observation_source.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_file_hash_builder.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(part_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(attempt_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_record_sequence.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(record_type.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_num.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(result_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_item_key.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(column_name.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_name.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(unit.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(lsl.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(usl.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(result_value.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(value_state.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_outcome.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(outcome_evidence.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_flag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(param_flag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(lot_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(wafer_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(site.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_x.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_y.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(part_txt.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_lot_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_wafer_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_die_x.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_die_y.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_stage.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(temperature.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(program_version.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_start_time.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_end_time.Finish(&arrays.emplace_back()));
  *batch = arrow::RecordBatch::Make(atvi_bucket_schema(),
                                    static_cast<std::int64_t>(rows.size()), arrays);
  return arrow::Status::OK();
}

arrow::Status write_attempt_facts_parquet(const fs::path& path,
                                          const std::vector<AtviAttempt>& attempts) {
  arrow::StringBuilder source_file_hash;
  arrow::StringBuilder source_record_id;
  arrow::Int64Builder part_index;
  arrow::Int32Builder attempt_index;
  arrow::Int64Builder source_record_sequence;
  arrow::StringBuilder project_type;
  arrow::StringBuilder test_stage;
  arrow::StringBuilder chip_uid;
  arrow::StringBuilder lot_id;
  arrow::StringBuilder wafer_id;
  arrow::StringBuilder site;
  arrow::Int32Builder die_x;
  arrow::Int32Builder die_y;
  arrow::StringBuilder part_txt;
  arrow::Int32Builder hbin;
  arrow::Int32Builder sbin;
  arrow::TimestampBuilder test_start_time(arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"),
                                          arrow::default_memory_pool());
  arrow::TimestampBuilder test_end_time(arrow::timestamp(arrow::TimeUnit::MICRO, "UTC"),
                                        arrow::default_memory_pool());
  arrow::StringBuilder temperature;
  arrow::StringBuilder program_version;
  arrow::StringBuilder temperature_source;
  arrow::StringBuilder program_version_source;
  arrow::StringBuilder ft_lot_ref;
  arrow::StringBuilder source_lot_id;
  arrow::StringBuilder source_wafer_id;
  arrow::Int32Builder source_die_x;
  arrow::Int32Builder source_die_y;
  arrow::StringBuilder attempt_id;
  arrow::Int64Builder attempt_sequence;
  arrow::StringBuilder duplicate_key;
  arrow::StringBuilder device_result;
  arrow::StringBuilder identity_resolution;
  arrow::ListBuilder identity_reason_codes(arrow::default_memory_pool(),
                                           std::make_shared<arrow::StringBuilder>());
  arrow::StringBuilder source_evidence_version;
  arrow::StringBuilder mapping_candidate_capture_state;
  arrow::StringBuilder mapping_evidence_product_version;
  arrow::StringBuilder mapping_evidence_product_sha256;
  for (const auto& attempt : attempts) {
    ARROW_RETURN_NOT_OK(source_file_hash.Append(attempt.source_file_hash));
    ARROW_RETURN_NOT_OK(source_record_id.Append(attempt.source_record_id));
    ARROW_RETURN_NOT_OK(part_index.Append(attempt.part_index));
    ARROW_RETURN_NOT_OK(attempt_index.Append(attempt.attempt_index));
    append_optional(&source_record_sequence, attempt.source_record_sequence);
    ARROW_RETURN_NOT_OK(project_type.Append(attempt.project_type));
    append_optional_string(&test_stage, attempt.test_stage);
    append_optional_string(&chip_uid, attempt.chip_uid);
    append_optional_string(&lot_id, attempt.lot_id);
    append_optional_string(&wafer_id, attempt.wafer_id);
    append_optional_string(&site, attempt.site);
    append_optional(&die_x, attempt.die_x);
    append_optional(&die_y, attempt.die_y);
    append_optional_string(&part_txt, attempt.part_txt);
    append_optional(&hbin, attempt.hbin);
    append_optional(&sbin, attempt.sbin);
    append_optional(&test_start_time, attempt.test_start_time_us);
    append_optional(&test_end_time, attempt.test_end_time_us);
    append_optional_string(&temperature, attempt.temperature);
    append_optional_string(&program_version, attempt.program_version);
    ARROW_RETURN_NOT_OK(temperature_source.Append(attempt.temperature_source));
    ARROW_RETURN_NOT_OK(program_version_source.Append(attempt.program_version_source));
    append_optional_string(&ft_lot_ref, attempt.ft_lot_ref);
    append_optional_string(&source_lot_id, attempt.source_lot_id);
    append_optional_string(&source_wafer_id, attempt.source_wafer_id);
    append_optional(&source_die_x, attempt.source_die_x);
    append_optional(&source_die_y, attempt.source_die_y);
    append_optional_string(&attempt_id, attempt.attempt_id);
    append_optional(&attempt_sequence, attempt.attempt_sequence);
    append_optional_string(&duplicate_key, attempt.duplicate_key);
    ARROW_RETURN_NOT_OK(device_result.Append(attempt.device_result));
    ARROW_RETURN_NOT_OK(identity_resolution.Append(attempt.identity_resolution));
    if (attempt.identity_reason_codes.empty()) {
      ARROW_RETURN_NOT_OK(identity_reason_codes.Append(false));
    } else {
      ARROW_RETURN_NOT_OK(identity_reason_codes.Append(true));
      auto* values = static_cast<arrow::StringBuilder*>(identity_reason_codes.value_builder());
      for (const auto& reason : attempt.identity_reason_codes) {
        ARROW_RETURN_NOT_OK(values->Append(reason));
      }
    }
    ARROW_RETURN_NOT_OK(source_evidence_version.Append(attempt.source_evidence_version));
    ARROW_RETURN_NOT_OK(
        mapping_candidate_capture_state.Append(attempt.mapping_candidate_capture_state));
    ARROW_RETURN_NOT_OK(mapping_evidence_product_version.AppendNull());
    ARROW_RETURN_NOT_OK(mapping_evidence_product_sha256.AppendNull());
  }
  std::vector<std::shared_ptr<arrow::Array>> arrays;
  ARROW_RETURN_NOT_OK(source_file_hash.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_record_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(part_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(attempt_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_record_sequence.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(project_type.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_stage.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(chip_uid.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(lot_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(wafer_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(site.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_x.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_y.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(part_txt.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(hbin.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(sbin.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_start_time.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(test_end_time.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(temperature.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(program_version.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(temperature_source.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(program_version_source.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(ft_lot_ref.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_lot_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_wafer_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_die_x.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_die_y.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(attempt_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(attempt_sequence.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(duplicate_key.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(device_result.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(identity_resolution.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(identity_reason_codes.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_evidence_version.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(mapping_candidate_capture_state.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(mapping_evidence_product_version.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(mapping_evidence_product_sha256.Finish(&arrays.emplace_back()));
  auto table = arrow::Table::Make(atvi_attempt_facts_schema(), arrays,
                                  static_cast<std::int64_t>(attempts.size()));
  PARQUET_ASSIGN_OR_THROW(auto outfile, arrow::io::FileOutputStream::Open(path.string()));
  PARQUET_THROW_NOT_OK(parquet::arrow::WriteTable(
      *table, arrow::default_memory_pool(), outfile, /*chunk_size=*/65536,
      parquet::WriterProperties::Builder().compression(arrow::Compression::ZSTD)
          ->compression_level(1)
          ->build()));
  return arrow::Status::OK();
}

arrow::Status write_mapping_evidence_parquet(
    const fs::path& path, const std::vector<AtviMappingEvidence>& evidence) {
  arrow::StringBuilder source_file_hash;
  arrow::StringBuilder source_record_id;
  arrow::Int64Builder part_index;
  arrow::Int32Builder attempt_index;
  arrow::StringBuilder evidence_kind;
  arrow::UInt32Builder candidate_index;
  arrow::StringBuilder evidence_origin;
  arrow::StringBuilder lot_tag;
  arrow::StringBuilder wafer_tag;
  arrow::StringBuilder die_x_tag;
  arrow::StringBuilder die_y_tag;
  arrow::StringBuilder lot_payload;
  arrow::StringBuilder wafer_payload;
  arrow::StringBuilder die_x_payload;
  arrow::StringBuilder die_y_payload;
  arrow::StringBuilder policy_input_version;
  for (const auto& row : evidence) {
    ARROW_RETURN_NOT_OK(source_file_hash.Append(row.source_file_hash));
    ARROW_RETURN_NOT_OK(source_record_id.Append(row.source_record_id));
    ARROW_RETURN_NOT_OK(part_index.Append(row.part_index));
    ARROW_RETURN_NOT_OK(attempt_index.Append(row.attempt_index));
    ARROW_RETURN_NOT_OK(evidence_kind.Append(row.evidence_kind));
    ARROW_RETURN_NOT_OK(candidate_index.Append(row.candidate_index));
    ARROW_RETURN_NOT_OK(evidence_origin.Append(row.evidence_origin));
    ARROW_RETURN_NOT_OK(lot_tag.Append(row.lot_tag));
    ARROW_RETURN_NOT_OK(wafer_tag.Append(row.wafer_tag));
    ARROW_RETURN_NOT_OK(die_x_tag.Append(row.die_x_tag));
    ARROW_RETURN_NOT_OK(die_y_tag.Append(row.die_y_tag));
    append_optional_string(&lot_payload, row.lot_payload);
    append_optional_string(&wafer_payload, row.wafer_payload);
    append_optional_string(&die_x_payload, row.die_x_payload);
    append_optional_string(&die_y_payload, row.die_y_payload);
    ARROW_RETURN_NOT_OK(policy_input_version.Append(row.policy_input_version));
  }
  std::vector<std::shared_ptr<arrow::Array>> arrays;
  ARROW_RETURN_NOT_OK(source_file_hash.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(source_record_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(part_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(attempt_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(evidence_kind.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(candidate_index.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(evidence_origin.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(lot_tag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(wafer_tag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_x_tag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_y_tag.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(lot_payload.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(wafer_payload.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_x_payload.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(die_y_payload.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(policy_input_version.Finish(&arrays.emplace_back()));
  auto table = arrow::Table::Make(atvi_mapping_evidence_schema(), arrays,
                                  static_cast<std::int64_t>(evidence.size()));
  PARQUET_ASSIGN_OR_THROW(auto outfile, arrow::io::FileOutputStream::Open(path.string()));
  PARQUET_THROW_NOT_OK(parquet::arrow::WriteTable(
      *table, arrow::default_memory_pool(), outfile, /*chunk_size=*/65536,
      parquet::WriterProperties::Builder().compression(arrow::Compression::ZSTD)
          ->compression_level(1)
          ->build()));
  return arrow::Status::OK();
}

arrow::Status write_locator_parquet(
    const fs::path& path,
    const std::map<std::array<std::uint8_t, 16>, AtviLocatorEntry>& locator) {
  arrow::FixedSizeBinaryBuilder key(arrow::fixed_size_binary(16));
  arrow::UInt16Builder bucket_id;
  arrow::UInt32Builder first_row_group;
  arrow::UInt32Builder row_group_count;
  arrow::Int64Builder row_count;
  arrow::Int64Builder finite_count;
  arrow::DoubleBuilder min;
  arrow::DoubleBuilder max;
  arrow::ListBuilder units(arrow::default_memory_pool(),
                           std::make_shared<arrow::StringBuilder>());
  arrow::StringBuilder content_sha256;
  for (const auto& entry : locator) {
    const auto& item = entry.second;
    ARROW_RETURN_NOT_OK(key.Append(entry.first.data()));
    ARROW_RETURN_NOT_OK(bucket_id.Append(item.bucket_id));
    ARROW_RETURN_NOT_OK(first_row_group.Append(item.first_row_group));
    ARROW_RETURN_NOT_OK(row_group_count.Append(item.row_group_count));
    ARROW_RETURN_NOT_OK(row_count.Append(item.row_count));
    ARROW_RETURN_NOT_OK(finite_count.Append(item.finite_count));
    if (item.min.has_value()) {
      ARROW_RETURN_NOT_OK(min.Append(*item.min));
    } else {
      ARROW_RETURN_NOT_OK(min.AppendNull());
    }
    if (item.max.has_value()) {
      ARROW_RETURN_NOT_OK(max.Append(*item.max));
    } else {
      ARROW_RETURN_NOT_OK(max.AppendNull());
    }
    if (item.units.empty()) {
      ARROW_RETURN_NOT_OK(units.AppendNull());
    } else {
      ARROW_RETURN_NOT_OK(units.Append(true));
      auto* values = static_cast<arrow::StringBuilder*>(units.value_builder());
      for (const auto& unit : item.units) {
        ARROW_RETURN_NOT_OK(values->Append(unit));
      }
    }
    ARROW_RETURN_NOT_OK(content_sha256.Append(item.content_sha256));
  }
  std::vector<std::shared_ptr<arrow::Array>> arrays;
  ARROW_RETURN_NOT_OK(key.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(bucket_id.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(first_row_group.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(row_group_count.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(row_count.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(finite_count.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(min.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(max.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(units.Finish(&arrays.emplace_back()));
  ARROW_RETURN_NOT_OK(content_sha256.Finish(&arrays.emplace_back()));
  auto table = arrow::Table::Make(atvi_locator_schema(), arrays,
                                  static_cast<std::int64_t>(locator.size()));
  PARQUET_ASSIGN_OR_THROW(auto outfile, arrow::io::FileOutputStream::Open(path.string()));
  PARQUET_THROW_NOT_OK(parquet::arrow::WriteTable(
      *table, arrow::default_memory_pool(), outfile, /*chunk_size=*/1));
  return arrow::Status::OK();
}

}  // namespace deepyield_stdf
