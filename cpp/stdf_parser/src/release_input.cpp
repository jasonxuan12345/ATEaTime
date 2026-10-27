#include "deepyield_stdf/release_import.hpp"
#include "deepyield_stdf/sha256.hpp"
#include <arrow/io/api.h>
#include <arrow/io/compressed.h>
#include <arrow/io/stdio.h>
#include <arrow/ipc/api.h>
#include <arrow/util/compression.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <iostream>
#if defined(_WIN32)
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
#include <fcntl.h>
#include <io.h>
#endif

namespace deepyield_stdf::release {
namespace {
template<class T> T take(arrow::Result<T> result) {
  if(!result.ok())throw std::runtime_error(result.status().ToString());
  return std::move(result).ValueOrDie();
}
void check(arrow::Status status) {if(!status.ok())throw std::runtime_error(status.ToString());}
// Hash the original (possibly compressed) bytes during the sole source pass.
class HashInput final: public arrow::io::InputStream {
 public:
  explicit HashInput(std::shared_ptr<arrow::io::InputStream> raw):raw_(std::move(raw)){}
  arrow::Status Close() override {return raw_->Close();}
  bool closed() const override {return raw_->closed();}
  arrow::Result<int64_t> Tell() const override {return raw_->Tell();}
  arrow::Result<int64_t> Read(int64_t n,void* out) override {
    ARROW_ASSIGN_OR_RAISE(auto count,raw_->Read(n,out));
    hash.update(out,size_t(count));return count;
  }
  arrow::Result<std::shared_ptr<arrow::Buffer>> Read(int64_t n) override {
    ARROW_ASSIGN_OR_RAISE(auto buffer,raw_->Read(n));
    hash.update(buffer->data(),size_t(buffer->size()));return buffer;
  }
  Sha256 hash;
 private:
  std::shared_ptr<arrow::io::InputStream> raw_;
};
void exact(arrow::io::InputStream& input,int64_t n,void* out) {
  if(take(input.Read(n,out))!=n)throw std::runtime_error("STDF 字段不完整");
}
}
int main(const std::string& supplied_source, bool calculation_only) {
  try {
    std::string source=supplied_source;
#if defined(_WIN32)
    _setmode(_fileno(stdout),_O_BINARY);
    int argc=0;
    auto argv=CommandLineToArgvW(GetCommandLineW(),&argc);
    if(!argv||argc!=3)throw std::runtime_error("Invalid Release command line");
    int size=WideCharToMultiByte(CP_UTF8,0,argv[2],-1,nullptr,0,nullptr,nullptr);
    source.resize(size);
    WideCharToMultiByte(CP_UTF8,0,argv[2],-1,source.data(),size,nullptr,nullptr);
    source.resize(size-1);
    LocalFree(argv);
#endif
    // Phase markers on stderr; the Python side forwards "release_native" lines
    // into the release perf log and ignores everything else.
    auto emit_phase=[&](const char* name,double elapsed_ms) {
      std::fprintf(stderr,"release_native {\"event\":\"phase\",\"name\":\"%s\",\"elapsed_ms\":%.1f}\n",
        name,elapsed_ms);
      std::fflush(stderr);
    };
    auto parse_started=std::chrono::steady_clock::now();
    auto raw=std::make_shared<HashInput>(take(arrow::io::ReadableFile::Open(source)));
    std::shared_ptr<arrow::io::InputStream> input=raw;
    auto lower=source;
    std::transform(lower.begin(),lower.end(),lower.begin(),[](unsigned char c){return char(std::tolower(c));});
    std::unique_ptr<arrow::util::Codec> codec;
    if(lower.size()>=3&&lower.substr(lower.size()-3)==".gz")
      codec=take(arrow::util::Codec::Create(arrow::Compression::GZIP));
    else if(lower.size()>=4&&lower.substr(lower.size()-4)==".bz2")
      codec=take(arrow::util::Codec::Create(arrow::Compression::BZ2));
    if(codec)input=take(arrow::io::CompressedInputStream::Make(codec.get(),raw));
    input=take(arrow::io::BufferedInputStream::Create(1024*1024,arrow::default_memory_pool(),input));
    uint8_t far_bytes[6];exact(*input,6,far_bytes);
    if(far_bytes[2]!=0||far_bytes[3]!=10||(far_bytes[4]!=1&&far_bytes[4]!=2)||far_bytes[5]!=4)
      throw std::runtime_error("需要以 FAR 开头的 STDF v4 文件");
    Parser parser(source);parser.little=far_bytes[4]==2;
    if((parser.little?(far_bytes[0]|(far_bytes[1]<<8)):((far_bytes[0]<<8)|far_bytes[1]))!=2)
      throw std::runtime_error("FAR 长度无效");
    parser.record(0,10,std::vector<uint8_t>{far_bytes[4],far_bytes[5]});
    std::vector<uint8_t> payload;
    for(;;) {
      uint8_t header[4];auto count=take(input->Read(4,header));
      if(!count)break;
      if(count!=4)throw std::runtime_error("STDF 字段不完整");
      int size=parser.little?(header[0]|(header[1]<<8)):((header[0]<<8)|header[1]);
      payload.resize(size);exact(*input,size,payload.data());
      parser.record(header[2],header[3],payload);
    }
    // A codec may stop before consuming trailing compressed bytes. Hash all of them.
    uint8_t tail[65536];
    while(take(raw->Read(sizeof(tail),tail))) {}
    parser.finish();
    emit_phase("parse",std::chrono::duration<double,std::milli>(
      std::chrono::steady_clock::now()-parse_started).count());
    auto build_started=std::chrono::steady_clock::now();
    auto output=std::make_shared<arrow::io::StdoutStream>();
    std::shared_ptr<arrow::ipc::RecordBatchWriter> writer;
    parser.visit_batches(raw->hash.hex_digest_current(),[&](std::shared_ptr<arrow::RecordBatch> batch) {
      if(!writer)writer=take(arrow::ipc::MakeStreamWriter(output,batch->schema()));
      check(writer->WriteRecordBatch(*batch));
    },64*1024*1024,65536,65536,true,calculation_only);
    check(writer->Close());check(output->Close());
    emit_phase("arrow_ipc",std::chrono::duration<double,std::milli>(
      std::chrono::steady_clock::now()-build_started).count());
    return 0;
  } catch(const std::exception& e) {
    std::cerr << e.what() << "\n";return 1;
  }
}
}  // namespace deepyield_stdf::release
