#pragma once
#include <arrow/api.h>
#include <cstdint>
#include <cstring>
#include <functional>
#include <cmath>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace deepyield_stdf::release {
struct Json {
  using Object = std::map<std::string, Json>;
  using Array = std::vector<Json>;
  std::variant<std::nullptr_t, bool, int64_t, double, std::string, Object, Array> value = nullptr;
  Json() = default;
  Json(std::nullptr_t) {}
  Json(bool v): value(v) {}
  Json(int v): value(int64_t(v)) {}
  Json(int64_t v): value(v) {}
  Json(double v): value(v) {}
  Json(const char* v): value(std::string(v)) {}
  Json(std::string v): value(std::move(v)) {}
  Json(Object v): value(std::move(v)) {}
  Json(Array v): value(std::move(v)) {}
  bool null() const { return std::holds_alternative<std::nullptr_t>(value); }
  std::string text() const { auto p=std::get_if<std::string>(&value); return p ? *p : ""; }
  int64_t integer() const { auto p=std::get_if<int64_t>(&value); return p ? *p : 0; }
  double number() const { auto p=std::get_if<double>(&value); return p ? *p : double(integer()); }
  Object& object() { return std::get<Object>(value); }
  const Object& object() const { return std::get<Object>(value); }
  Array& array() { return std::get<Array>(value); }
  Json& operator[](const std::string& k) { return object()[k]; }
  const Json& get(const std::string& k) const {
    static const Json empty;
    auto p=object().find(k); return p==object().end() ? empty : p->second;
  }
  std::string dump() const;
};
struct Reader {
  const std::vector<uint8_t>& bytes;
  bool little;
  size_t at=0;
  size_t left() const { return bytes.size()-at; }
  void skip(size_t n) { if(n>left()) throw std::runtime_error("STDF 字段不完整"); at+=n; }
  uint64_t uintn(size_t n) {
    if(n>left()) throw std::runtime_error("STDF 字段不完整");
    uint64_t result=0;
    for(size_t i=0;i<n;++i) result|=uint64_t(bytes[at+i]) << (8*(little?i:n-i-1));
    at+=n; return result;
  }
  int u1() { return int(uintn(1)); }
  int u2() { return int(uintn(2)); }
  int64_t u4() { return int64_t(uintn(4)); }
  int i1() { return int(int8_t(u1())); }
  int i2() { return int(int16_t(u2())); }
  double r4() { uint32_t bits=uint32_t(uintn(4)); float v; std::memcpy(&v,&bits,4); return double(v); }
  std::string c1() { return left()?std::string(1,char(u1())):""; }
  Json text();
  std::string cn() {
    auto result=text().text();
    auto first=result.find_first_not_of('\0'),last=result.find_last_not_of('\0');
    return first==std::string::npos?"":result.substr(first,last-first+1);
  }
  Json optional_int(int n) { return left()?Json(n==1?int64_t(u1()):n==2?int64_t(u2()):u4()):Json(); }
};
struct Identity {
  std::string kind, name;
  int64_t number=0;
  std::map<int,std::string> columns;
  Json defaults=Json::Object{};
  std::shared_ptr<const Json> omitted;
  std::shared_ptr<const std::string> omitted_raw;
  // Defaults are immutable after the first definition. Cache each complete
  // optional payload, not the previous effective state (overrides are local).
  std::map<std::string, std::pair<std::shared_ptr<const Json>,
      std::shared_ptr<const std::string>>> optional_specs;
};
struct Event {
  Identity* identity=nullptr;
  std::string column;
  int index=-1, test_flag=0, param_flag=0;
  int64_t sequence=0;
  std::optional<double> result;
  bool valid=false;
  Json text, alarm, name;
  std::shared_ptr<const Json> effective;
  std::shared_ptr<const std::string> raw;
};
struct Part {
  Json fields=Json::Object{};
  std::vector<Event> events;
};
class Parser {
 public:
  explicit Parser(std::string source): source_(std::move(source)) {}
  void record(int type,int sub, const std::vector<uint8_t>& payload);
  void finish();
  // Bound Arrow builders before writing; IPC row slicing alone cannot prevent
  // a string child array from overflowing its 32-bit offsets.
  std::shared_ptr<arrow::Table> table(const std::string& hash,
      int64_t batch_bytes=64*1024*1024, int64_t batch_events=65536,
      int64_t batch_rows=65536);
  void visit_batches(const std::string& hash,
      const std::function<void(std::shared_ptr<arrow::RecordBatch>)>& consume,
      int64_t batch_bytes=64*1024*1024, int64_t batch_events=65536,
      int64_t batch_rows=65536, bool release_parts=false, bool calculation_only=false);
  bool little=true;
 private:
  std::string source_, lot_, wafer_;
  int64_t sequence_=0, part_index_=0;
  Json metadata_=Json::Object{{"mir",Json::Object{}},{"wafers",Json::Array{}},
    {"wrr",Json::Array{}},{"mrr",Json::Object{}},{"record_counts",Json::Object{}},
    {"warnings",Json::Array{}}};
  std::map<std::pair<int,int>,Part> contexts_;
  std::vector<Part> parts_;
  std::map<std::pair<std::string,int64_t>,Identity> identities_;
  std::map<std::string,Json> catalog_;
  std::vector<std::string> column_order_;
  std::map<std::pair<std::string,int64_t>,std::set<std::string>> summaries_;
  std::map<std::string,std::shared_ptr<const Json>> specs_;
  std::map<std::string,std::shared_ptr<const std::string>> raw_specs_;
  Part& context(int head,int site);
  Identity& identity(const std::string& kind,int64_t number,const Json& name);
  Json optional(Reader& r,const std::string& kind,int rtn);
  std::shared_ptr<const Json> effective(Identity& id,const Json& raw);
  void parametric(Reader& r,const std::string& kind);
  void functional(Reader& r);
  void part(Reader& r);
  void summary(Reader& r);
  void metadata(Reader& r,const std::string& kind);
  void warning(const std::string& message) { metadata_["warnings"].array().emplace_back(message); }
};
int main(const std::string& source, bool calculation_only=false);
}  // namespace deepyield_stdf::release
