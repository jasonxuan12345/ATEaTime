#include "deepyield_stdf/release_import.hpp"
#include <arrow/array/builder_dict.h>
#include <arrow/array/builder_nested.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <unordered_map>
namespace deepyield_stdf::release {
namespace {
void check(arrow::Status status) { if(!status.ok())throw std::runtime_error(status.ToString()); }
void append(arrow::ArrayBuilder* b,const Json& v) {
  if(v.null()) {check(b->AppendNull());return;}
  switch(b->type()->id()) {
    case arrow::Type::STRING: check(static_cast<arrow::StringBuilder*>(b)->Append(v.text()));break;
    case arrow::Type::INT64: check(static_cast<arrow::Int64Builder*>(b)->Append(v.integer()));break;
    case arrow::Type::DOUBLE: check(static_cast<arrow::DoubleBuilder*>(b)->Append(v.number()));break;
    case arrow::Type::BOOL: check(static_cast<arrow::BooleanBuilder*>(b)->Append(std::get<bool>(v.value)));break;
    case arrow::Type::LIST: {
      auto list=static_cast<arrow::ListBuilder*>(b);check(list->Append());
      for(auto& item:std::get<Json::Array>(v.value))append(list->value_builder(),item);
      break;
    }
    default:throw std::runtime_error("Unsupported Release Arrow field");
  }
}
std::shared_ptr<arrow::Array> finish_array(arrow::ArrayBuilder* b) {
  std::shared_ptr<arrow::Array> array;check(b->Finish(&array));return array;
}
}
// Include nested payloads and numeric buffers in the budget. This bounds every
// string child buffer as well as the total working set of the current batch.
int64_t payload_bytes(const Json& value) {
  if(auto text=std::get_if<std::string>(&value.value))return text->size()+8;
  if(auto array=std::get_if<Json::Array>(&value.value)) {
    int64_t size=8;for(const auto& item:*array)size+=payload_bytes(item);return size;
  }
  return 8;
}
int64_t part_bytes(const Part& part, size_t wide_columns) {
  int64_t size=wide_columns*9+256;
  for(const auto& field:part.fields.object())size+=payload_bytes(field.second);
  for(const auto& e:part.events) {
    size+=512+e.column.size()+e.identity->name.size()+e.identity->kind.size();
    size+=payload_bytes(e.text)+payload_bytes(e.alarm)+payload_bytes(e.name);
    if(e.raw)size+=e.raw->size();
    for(const auto& field:e.effective->object())size+=payload_bytes(field.second);
  }
  return size;
}
std::shared_ptr<arrow::Table> Parser::table(const std::string& hash,
    int64_t batch_bytes, int64_t batch_events, int64_t batch_rows) {
  std::vector<std::shared_ptr<arrow::RecordBatch>> batches;
  visit_batches(hash,[&](std::shared_ptr<arrow::RecordBatch> batch) {
    batches.push_back(std::move(batch));
  },batch_bytes,batch_events,batch_rows);
  auto result=arrow::Table::FromRecordBatches(batches);
  if(!result.ok())throw std::runtime_error(result.status().ToString());
  auto table=std::move(result).ValueOrDie();check(table->ValidateFull());return table;
}
void Parser::visit_batches(const std::string& hash,
    const std::function<void(std::shared_ptr<arrow::RecordBatch>)>& consume,
    int64_t batch_bytes, int64_t batch_events, int64_t batch_rows, bool release_parts, bool calculation_only) {
  if(batch_bytes<=0||batch_events<=0||batch_rows<=0)
    throw std::runtime_error("Release Arrow batch limits must be positive");
  using arrow::field;
  auto str=arrow::utf8(),integer=arrow::int64(),real=arrow::float64();
  std::vector<std::shared_ptr<arrow::Field>> event_fields;
  for(auto name:{"column","record_type","name","test_text","alarm_id","unit","units_in",
      "c_resfmt","c_llmfmt","c_hlmfmt","raw_optional_json"})event_fields.push_back(field(name,str));
  for(auto name:{"test_num","result_index","test_flag","param_flag","record_sequence",
      "opt_flag","res_scal","llm_scal","hlm_scal"})event_fields.push_back(field(name,integer));
  for(auto name:{"result","low_limit","high_limit","low_spec","high_spec","start_in","incr_in"})
    event_fields.push_back(field(name,real));
  event_fields.push_back(field("rtn_indx",arrow::list(integer)));
  event_fields.push_back(field("result_valid",arrow::boolean()));
  if(calculation_only) {
    const std::set<std::string> needed={"column","record_type","name","unit","test_num",
      "result_index","test_flag","param_flag","record_sequence","result","low_limit",
      "high_limit","result_valid"};
    event_fields.erase(std::remove_if(event_fields.begin(),event_fields.end(),
      [&](const auto& f){return !needed.count(f->name());}),event_fields.end());
    // Identity strings repeat for nearly every event: dictionary types keep
    // one copy per distinct value on the wire and in memory.
    auto identity=arrow::dictionary(arrow::int32(),arrow::utf8());
    for(auto& f:event_fields)if(f->name()=="column"||f->name()=="record_type"||
        f->name()=="name"||f->name()=="unit")f=field(f->name(),identity);
    // Match Python's PRR-local last-execution rule: retain first on equal
    // sequence, and preserve each column's first-appearance order. Never
    // discard a final invalid result in favour of an earlier valid one.
    for(auto& part:parts_) {
      std::unordered_map<std::string,size_t> slots;
      std::vector<Event> selected;
      for(auto& event:part.events) {
        auto [entry,inserted]=slots.emplace(event.column,selected.size());
        if(inserted)selected.push_back(std::move(event));
        else if(event.sequence>selected[entry->second].sequence)
          selected[entry->second]=std::move(event);
      }
      part.events=std::move(selected);
    }
  }
  auto event_type=arrow::struct_(event_fields);
  std::vector<std::shared_ptr<arrow::Field>> fields={field("__test_records",arrow::list(event_type))};
  std::vector<std::shared_ptr<arrow::Field>> part_fields;
  for(auto name:{"Name","LOT_ID","WAFER_ID","SOURCE_FILE","STDF_PART_ID","PART_TXT",
      "PASS_FAIL","__conditions_json"})part_fields.push_back(field(name,str));
  for(auto name:{"__part_index","HEAD_NUM","SITE","__source_record_sequence","HBIN","SBIN",
      "DIE_X","DIE_Y","TEST_TIME","PART_FLAG"})part_fields.push_back(field(name,integer));
  part_fields.push_back(field("__missing_fields",arrow::list(str)));
  if(calculation_only) {
    const std::set<std::string> unused={"SOURCE_FILE","PART_TXT","PASS_FAIL","__missing_fields"};
    part_fields.erase(std::remove_if(part_fields.begin(),part_fields.end(),
      [&](const auto& f){return unused.count(f->name());}),part_fields.end());
  }
  fields.insert(fields.end(),part_fields.begin(),part_fields.end());
  Json::Array tests;
  for(auto& col:column_order_) {
    tests.push_back(catalog_.at(col));
    if(!calculation_only)fields.push_back(field(col,real));
  }
  metadata_["source_file"]=source_;
  Json meta=Json::Object{{"schema_version",calculation_only?"deepyield-release-calculation-v1":"deepyield-release-wide-all-v1"},
    {"source_sha256",hash},{"source_file",source_},{"row_count",int64_t(parts_.size())},
    {"test_catalog",tests},{"stdf_metadata",metadata_},{"all_attempts_preserved",!calculation_only},
    {"retest_policy",calculation_only?"last_execution_per_column_within_prr":"not_selected"},{"value_units","STDF base units"},
    {"parser","deepyield-release-cpp-v1"}};
  auto schema=arrow::schema(fields,arrow::key_value_metadata({"deepyield.release"},{meta.dump()}));
  // Finish parsing first: trailing TSR and late test names still apply to all
  // parts. Materialize only one bounded group of parts into builders at a time.
  int64_t emitted_rows=0,emitted_events=0;double consume_ms=0;
  for(size_t begin=0;begin<parts_.size();) {
    size_t end=begin;int64_t bytes=0,event_count=0;
    while(end<parts_.size()&&int64_t(end-begin)<batch_rows) {
      const auto& part=parts_[end];auto size=part_bytes(part,calculation_only?0:column_order_.size());
      if(end>begin&&(bytes+size>batch_bytes||event_count+int64_t(part.events.size())>batch_events))break;
      if(size>=2147483646LL)
        throw std::runtime_error("单条芯片记录的展开数据超过 Arrow 安全容量，无法按完整记录分批");
      bytes+=size;event_count+=part.events.size();++end;
    }
    emitted_rows+=end-begin;emitted_events+=event_count;
  auto builder_result=arrow::MakeBuilder(arrow::list(event_type));
  if(!builder_result.ok())throw std::runtime_error(builder_result.status().ToString());
  auto event_builder=std::move(builder_result).ValueOrDie();
  auto list=static_cast<arrow::ListBuilder*>(event_builder.get());
  auto events=static_cast<arrow::StructBuilder*>(list->value_builder());
  // Typed builders and a shared-pointer spec cache for the calculation path:
  // no Json temporaries, no per-event string map lookups. FTR effective specs
  // are fresh empty objects per event, so only PTR/MPR may populate the cache.
  struct Limits {std::string unit;bool has_unit=false;double low=0,high=0;
    bool has_low=false,has_high=false;};
  std::unordered_map<const Json*,Limits> spec_cache;
  auto limits=[&](const Json& effective)->const Limits& {
    auto found=spec_cache.find(&effective);
    if(found==spec_cache.end()) {
      Limits value;
      auto unit=effective.get("unit");
      if(!unit.null()){value.has_unit=true;value.unit=unit.text();}
      auto low=effective.get("low_limit");
      if(!low.null()){value.has_low=true;value.low=low.number();}
      auto high=effective.get("high_limit");
      if(!high.null()){value.has_high=true;value.high=high.number();}
      found=spec_cache.emplace(&effective,value).first;
    }
    return found->second;
  };
  auto typed=[&](const char* target)->arrow::ArrayBuilder* {
    for(int i=0;i<int(event_fields.size());++i)
      if(event_fields[i]->name()==target)return events->field_builder(i);
    return nullptr; };
  auto column_b=static_cast<arrow::DictionaryBuilder<arrow::StringType>*>(typed("column"));
  auto kind_b=static_cast<arrow::DictionaryBuilder<arrow::StringType>*>(typed("record_type"));
  auto name_b=static_cast<arrow::DictionaryBuilder<arrow::StringType>*>(typed("name"));
  auto unit_b=static_cast<arrow::DictionaryBuilder<arrow::StringType>*>(typed("unit"));
  auto num_b=static_cast<arrow::Int64Builder*>(typed("test_num"));
  auto index_b=static_cast<arrow::Int64Builder*>(typed("result_index"));
  auto tflag_b=static_cast<arrow::Int64Builder*>(typed("test_flag"));
  auto pflag_b=static_cast<arrow::Int64Builder*>(typed("param_flag"));
  auto seq_b=static_cast<arrow::Int64Builder*>(typed("record_sequence"));
  auto result_b=static_cast<arrow::DoubleBuilder*>(typed("result"));
  auto low_b=static_cast<arrow::DoubleBuilder*>(typed("low_limit"));
  auto high_b=static_cast<arrow::DoubleBuilder*>(typed("high_limit"));
  auto valid_b=static_cast<arrow::BooleanBuilder*>(typed("result_valid"));
  std::vector<std::unordered_map<std::string,Json>> wide;
  wide.reserve(end-begin);
  for(size_t row=begin;row<end;++row) {
    const auto& p=parts_[row];
    check(list->Append());
    if(!calculation_only)wide.emplace_back();
    for(auto& e:p.events) {
      check(events->Append());
      if(calculation_only) {
        check(column_b->Append(e.column));
        check(kind_b->Append(e.identity->kind));
        if(e.identity->kind=="FTR") {
          if(e.name.null())check(name_b->AppendNull());else check(name_b->Append(e.name.text()));
          check(unit_b->AppendNull());check(low_b->AppendNull());check(high_b->AppendNull());
        } else {
          if(e.identity->name.empty())check(name_b->AppendNull());
          else check(name_b->Append(e.identity->name));
          const auto& limit=limits(*e.effective);
          if(limit.has_unit)check(unit_b->Append(limit.unit));else check(unit_b->AppendNull());
          if(limit.has_low)check(low_b->Append(limit.low));else check(low_b->AppendNull());
          if(limit.has_high)check(high_b->Append(limit.high));else check(high_b->AppendNull());
        }
        check(num_b->Append(e.identity->number));
        if(e.index<0)check(index_b->AppendNull());else check(index_b->Append(e.index));
        check(tflag_b->Append(e.test_flag));
        if(e.identity->kind=="FTR")check(pflag_b->AppendNull());
        else check(pflag_b->Append(e.param_flag));
        check(seq_b->Append(e.sequence));
        if(e.result)check(result_b->Append(*e.result));else check(result_b->AppendNull());
        check(valid_b->Append(e.valid));
        continue;
      }
      // Shared definitions are materialized directly into Arrow, never Python dicts.
      for(int i=0;i<int(event_fields.size());++i) {
        const auto& name=event_fields[i]->name();
        Json value;
        if(name=="column")value=e.column;
        else if(name=="record_type")value=e.identity->kind;
        else if(name=="test_num")value=e.identity->number;
        else if(name=="name")value=e.identity->kind=="FTR"?e.name:
            (e.identity->name.empty()?Json():Json(e.identity->name));
        else if(name=="result_index")value=e.index<0?Json():Json(e.index);
        else if(name=="test_flag")value=e.test_flag;
        else if(name=="param_flag")value=e.identity->kind=="FTR"?Json():Json(e.param_flag);
        else if(name=="record_sequence")value=e.sequence;
        else if(name=="result")value=e.result?Json(*e.result):Json();
        else if(name=="result_valid")value=e.valid;
        else if(name=="test_text")value=e.text;
        else if(name=="alarm_id")value=e.alarm;
        else if(name=="raw_optional_json")value=e.raw?Json(*e.raw):Json();
        else {append(events->field_builder(i),e.effective->get(name));continue;}
        append(events->field_builder(i),value);
      }
      if(!calculation_only)wide.back()[e.column]=e.result?Json(*e.result):Json();
    }
  }
  std::vector<std::shared_ptr<arrow::Array>> arrays={finish_array(event_builder.get())};
  for(auto& f:part_fields) {
    auto result=arrow::MakeBuilder(f->type());
    if(!result.ok())throw std::runtime_error(result.status().ToString());
    auto builder=std::move(result).ValueOrDie();
    for(size_t row=begin;row<end;++row)append(builder.get(),parts_[row].fields.get(f->name()));
    arrays.push_back(finish_array(builder.get()));
  }
  if(!calculation_only)for(auto& col:column_order_) {
    arrow::DoubleBuilder builder;
    for(auto& row:wide) {
      auto v=row.find(col);
      append(&builder,v==row.end()?Json():v->second);
    }
    arrays.push_back(finish_array(&builder));
  }
    auto batch=arrow::RecordBatch::Make(schema,end-begin,arrays);
    check(batch->ValidateFull());
    auto consume_started=std::chrono::steady_clock::now();
    consume(std::move(batch));
    consume_ms+=std::chrono::duration<double,std::milli>(
      std::chrono::steady_clock::now()-consume_started).count();
    if(release_parts) {
      // Trailing definitions have already been resolved. Once emitted, a part
      // is no longer needed by this one-shot import or the IPC writer.
      for(size_t row=begin;row<end;++row) {
        std::vector<Event>().swap(parts_[row].events);
        parts_[row].fields=Json::Object{};
      }
    }
    if(end==parts_.size())break;
    begin=end;
  }
  std::fprintf(stderr,"release_native {\"event\":\"stats\",\"rows\":%lld,\"events\":%lld,"
    "\"consume_ms\":%.1f}\n",static_cast<long long>(emitted_rows),
    static_cast<long long>(emitted_events),consume_ms);
  std::fflush(stderr);
}
}  // namespace deepyield_stdf::release
