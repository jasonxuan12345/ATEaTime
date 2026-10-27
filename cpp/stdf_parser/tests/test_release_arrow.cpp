#include "deepyield_stdf/release_import.hpp"
#include <iostream>
#include <stdexcept>
using namespace deepyield_stdf::release;
void require(bool value,const char* message) {if(!value)throw std::runtime_error(message);}
void uintn(std::vector<uint8_t>& out,uint32_t value,int n) {
  for(int i=0;i<n;++i)out.push_back(uint8_t(value>>(i*8)));
}
void text(std::vector<uint8_t>& out,const std::string& value) {
  out.push_back(uint8_t(value.size()));out.insert(out.end(),value.begin(),value.end());
}
int main() {
  try {
    // Feed synthetic records directly to the parser: no source files are read.
    Parser parser("synthetic-only");parser.record(0,10,{2,4});
    for(int p=0;p<9;++p) {
      parser.record(5,10,{1,uint8_t(p%3)});
      for(int repeat=0;repeat<5;++repeat) {
        std::vector<uint8_t> ptr;
        uintn(ptr,1001,4);ptr.insert(ptr.end(),{1,uint8_t(p%3),0,0});
        float value=float(p+repeat);uint32_t bits;std::memcpy(&bits,&value,4);uintn(ptr,bits,4);
        text(ptr,p?"VDD":"");text(ptr,"");parser.record(15,10,ptr);
      }
      std::vector<uint8_t> prr={1,uint8_t(p%3),0};
      for(int value:{5,1,1,10,20})uintn(prr,value,2);
      uintn(prr,100,4);text(prr,"P"+std::to_string(p));text(prr,"");
      parser.record(5,20,prr);
    }
    parser.finish();auto full=parser.table("hash");
    auto events=parser.table("hash",64*1024*1024,10,65536);
    auto rows=parser.table("hash",64*1024*1024,65536,2);
    auto bytes=parser.table("hash",1,65536,65536);
    require(events->column(0)->num_chunks()==5,"event budget did not split builders");
    require(rows->column(0)->num_chunks()==5,"row budget did not split builders");
    require(bytes->column(0)->num_chunks()==9,"byte budget did not split builders");
    for(auto table:{events,rows,bytes}) {
      require(table->ValidateFull().ok(),"invalid chunked table");
      require(full->Equals(*table,true),"batching changed rows, events or metadata");
    }
    std::vector<std::shared_ptr<arrow::RecordBatch>> streamed;
    parser.visit_batches("hash",[&](std::shared_ptr<arrow::RecordBatch> batch) {
      streamed.push_back(std::move(batch));
    },64*1024*1024,10,65536,false);
    auto result=arrow::Table::FromRecordBatches(streamed);
    require(result.ok()&&full->Equals(**result,true),"streaming changed executions or late metadata");
    std::vector<std::shared_ptr<arrow::RecordBatch>> compact;
    parser.visit_batches("hash",[&](std::shared_ptr<arrow::RecordBatch> batch) {
      compact.push_back(std::move(batch));
    },64*1024*1024,3,65536,true,true);
    auto calculated=arrow::Table::FromRecordBatches(compact).ValueOrDie();
    require(calculated->num_rows()==9,"calculation mode lost PRRs");
    require(calculated->schema()->GetFieldIndex("TEST_1001")==-1,"calculation mode emitted wide values");
    int part=0;
    for(const auto& batch:compact) {
      auto lists=std::static_pointer_cast<arrow::ListArray>(batch->column(0));
      auto events=std::static_pointer_cast<arrow::StructArray>(lists->values());
      require(events->GetFieldByName("raw_optional_json")==nullptr,"calculation mode emitted raw payloads");
      auto values=std::static_pointer_cast<arrow::DoubleArray>(events->GetFieldByName("result"));
      auto names=std::static_pointer_cast<arrow::DictionaryArray>(events->GetFieldByName("name"));
      auto name_values=std::static_pointer_cast<arrow::StringArray>(names->dictionary());
      auto name_codes=std::static_pointer_cast<arrow::Int32Array>(names->indices());
      for(int row=0;row<batch->num_rows();++row,++part) {
        require(lists->value_length(row)==1,"calculation mode retained repeated executions");
        require(values->Value(lists->value_offset(row))==part+4,"calculation mode selected wrong execution");
        require(name_values->GetString(name_codes->Value(lists->value_offset(row)))=="VDD","calculation mode lost late name");
      }
    }
    Parser empty("empty");empty.record(0,10,{2,4});bool rejected=false;
    try {empty.finish();}catch(const std::exception&) {rejected=true;}
    require(rejected,"empty input must still be rejected");
    std::cout<<"Release batches preserve all parts, repeated events, late names and metadata\n";
    return 0;
  } catch(const std::exception& e) {std::cerr<<e.what()<<"\n";return 1;}
}
