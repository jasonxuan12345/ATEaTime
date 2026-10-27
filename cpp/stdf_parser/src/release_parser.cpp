#include "deepyield_stdf/release_import.hpp"
#include <algorithm>
#include <iomanip>
#include <sstream>
namespace deepyield_stdf::release {
namespace {
std::string quote(const std::string& text) {
  std::ostringstream out; out << '"';
  for(unsigned char c:text) {
    if(c=='"'||c=='\\') out << '\\' << c;
    else if(c<32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
    else out << c;
  }
  return out.str()+'"';
}
bool named(const Json& v) { return !v.text().empty() && v.text()!=std::string(1,'\0'); }
std::string column(int64_t number,const std::string& name,int index) {
  std::string safe;
  for(unsigned char c:name) {
    bool ascii=(c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9');
    if(ascii) safe+=c;
    else if(!safe.empty()&&safe.back()!='_') safe+='_';
  }
  if(!safe.empty()&&safe.back()=='_') safe.pop_back();
  std::string result=(safe.empty()||safe=="UNKNOWN")?"TEST_"+std::to_string(number):std::to_string(number)+"_"+safe;
  return index<0?result:result+"_"+std::to_string(index);
}
}
std::string Json::dump() const {
  if(null()) return "null";
  if(auto p=std::get_if<bool>(&value)) return *p?"true":"false";
  if(auto p=std::get_if<int64_t>(&value)) return std::to_string(*p);
  if(auto p=std::get_if<double>(&value)) {
    if(!std::isfinite(*p))return "null";
    std::ostringstream out;out<<std::setprecision(17)<<*p;return out.str();
  }
  if(auto p=std::get_if<std::string>(&value)) return quote(*p);
  std::string out;
  if(auto p=std::get_if<Object>(&value)) {
    out="{";
    for(auto& [k,v]:*p) {if(out.size()>1)out+=",";out+=quote(k)+":"+v.dump();}
    return out+"}";
  }
  out="[";
  for(auto& v:std::get<Array>(value)) {if(out.size()>1)out+=",";out+=v.dump();}
  return out+"]";
}
Json Reader::text() {
  if(!left()) return {};
  auto n=u1(); if(!n) return {};
  if(size_t(n)>left()) throw std::runtime_error("STDF 字段不完整");
  // Preserve the existing Latin-1 interpretation as valid Arrow UTF-8.
  std::string result;
  for(int i=0;i<n;++i) {
    unsigned char c=bytes[at++];
    if(c<128) result+=char(c);
    else {result+=char(0xc0|(c>>6));result+=char(0x80|(c&63));}
  }
  return result;
}
Part& Parser::context(int head,int site) {
  auto key=std::make_pair(head,site);
  auto found=contexts_.find(key);
  if(found!=contexts_.end())return found->second;
  Part p;
  p.fields=Json::Object{{"__part_index",++part_index_},{"SITE",site},{"HEAD_NUM",head},
    {"LOT_ID",lot_},{"WAFER_ID",wafer_},{"SOURCE_FILE",source_}};
  return contexts_.emplace(key,std::move(p)).first->second;
}
Identity& Parser::identity(const std::string& kind,int64_t number,const Json& name) {
  auto [it,created]=identities_.try_emplace({kind,number});
  auto& id=it->second;
  if(created) {id.kind=kind;id.number=number;}
  if((id.name.empty()||id.name==std::string(1,'\0'))&&named(name)) id.name=name.text();
  return id;
}
Json Parser::optional(Reader& r,const std::string& kind,int rtn) {
  Json raw=Json::Object{{"opt_flag",r.optional_int(1)}};
  for(auto name:{"res_scal","llm_scal","hlm_scal"}) raw[name]=r.left()?Json(r.i1()):Json();
  for(auto name:{"low_limit","high_limit"}) raw[name]=r.left()?Json(r.r4()):Json();
  if(kind=="MPR") {
    for(auto name:{"start_in","incr_in"}) raw[name]=r.left()?Json(r.r4()):Json();
    raw["rtn_indx"]=Json();
    if(r.left()) {
      Json::Array indices;
      for(int i=0;i<rtn;++i) indices.emplace_back(r.u2());
      raw["rtn_indx"]=indices;
    }
  }
  raw["unit"]=r.text();
  if(kind=="MPR") raw["units_in"]=r.text();
  for(auto name:{"c_resfmt","c_llmfmt","c_hlmfmt"}) raw[name]=r.text();
  for(auto name:{"low_spec","high_spec"}) raw[name]=r.left()?Json(r.r4()):Json();
  if(r.left())throw std::runtime_error(kind+" 存在无法解释的额外字段");
  return raw;
}
std::shared_ptr<const Json> Parser::effective(Identity& id,const Json& raw) {
  bool first=id.defaults.object().empty();
  Json value=id.defaults;
  const auto flag=raw.get("opt_flag");
  const std::map<std::string,int> masks={{"res_scal",1},{"llm_scal",0x50},{"low_limit",0x50},
    {"hlm_scal",0xa0},{"high_limit",0xa0},{"low_spec",4},{"high_spec",8},{"start_in",2},{"incr_in",2}};
  for(auto& [name,v]:raw.object()) {
    if(name=="opt_flag"||v.null())continue;
    auto found=masks.find(name);
    int mask=found==masks.end()?0:found->second;
    if(!flag.null()&&(flag.integer()&mask))continue;
    bool invalid_float=std::holds_alternative<double>(v.value)&&!std::isfinite(v.number());
    value[name]=(v.text()==std::string(1,'\0')||invalid_float)?Json():v;
  }
  for(auto& [name,mask]:std::map<std::string,int>{{"low_limit",0x40},{"high_limit",0x80},{"low_spec",4},{"high_spec",8}})
    if(!flag.null()&&(flag.integer()&mask))value[name]=Json();
  value["opt_flag"]=flag;
  if(first)id.defaults=value;
  auto serialized=value.dump();
  auto& cached=specs_[serialized];
  if(!cached)cached=std::make_shared<const Json>(std::move(value));
  return cached;
}
void Parser::parametric(Reader& r,const std::string& kind) {
  auto number=r.u4(); int head=r.u1(),site=r.u1(),tf=r.u1(),pf=r.u1(),rtn=0,count=1;
  if(kind=="MPR") {rtn=r.u2();count=r.u2();r.skip((rtn+1)/2);}
  std::vector<double> values; values.reserve(count);
  for(int i=0;i<count;++i)values.push_back(r.r4());
  Json text=r.text(),alarm=r.text();
  auto& id=identity(kind,number,text);
  const bool omitted=!r.left();
  std::string optional_key(reinterpret_cast<const char*>(&rtn),sizeof(rtn));
  if(r.left())optional_key.append(reinterpret_cast<const char*>(r.bytes.data()+r.at),r.left());
  std::shared_ptr<const Json> spec;
  std::shared_ptr<const std::string> raw_ptr;
  if(omitted&&id.omitted) {
    spec=id.omitted;raw_ptr=id.omitted_raw;
  } else if(auto entry=id.optional_specs.find(optional_key);entry!=id.optional_specs.end()) {
    spec=entry->second.first;raw_ptr=entry->second.second;
    r.skip(r.left());
  } else {
    Json raw=optional(r,kind,rtn);
    spec=effective(id,raw);
    auto raw_text=raw.dump();
    auto& cached=raw_specs_[raw_text];
    if(!cached)cached=std::make_shared<const std::string>(std::move(raw_text));
    raw_ptr=cached;
    id.optional_specs.emplace(std::move(optional_key),std::make_pair(spec,raw_ptr));
    if(omitted) {id.omitted=spec;id.omitted_raw=raw_ptr;}
  }
  if((tf&0x10)&&pf==0)return;
  auto& p=context(head,site);
  for(int i=0;i<count;++i) {
    int index=kind=="MPR"?i:-1;
    auto [it,added]=id.columns.try_emplace(index,column(number,id.name,index));
    Event e;
    e.identity=&id;e.column=it->second;e.index=index;e.test_flag=tf;e.param_flag=pf;
    e.sequence=sequence_;e.result=values[i];e.valid=std::isfinite(values[i])&&!(tf&0x3f||pf&7);
    e.text=text;e.alarm=alarm;e.effective=spec;e.raw=raw_ptr;
    p.events.push_back(std::move(e));
    if(!catalog_.count(it->second)) {
      Json test=id.defaults;
      test["record_type"]=kind;test["test_num"]=number;test["name"]=named(Json(id.name))?Json(id.name):Json();
      test["column"]=it->second;test["result_index"]=index<0?Json():Json(index);
      catalog_[it->second]=test;column_order_.push_back(it->second);
    }
  }
}
void Parser::functional(Reader& r) {
  auto number=r.u4();int head=r.u1(),site=r.u1(),flag=r.u1();
  if(r.left())r.u1();
  for(int size:{4,4,4,4,4,4,2})if(r.left())r.skip(size);
  int rtn=r.left()?r.u2():0,pgm=r.left()?r.u2():0;
  if(r.left()) {r.skip(rtn*2+(rtn+1)/2+pgm*2+(pgm+1)/2);auto bits=r.u2();r.skip((bits+7)/8);}
  std::vector<Json> text;for(int i=0;i<7;++i)text.push_back(r.text());
  auto& id=identity("FTR",number,text[3]);
  std::string col=column(number,text[3].text(),-1);
  Event e;e.identity=&id;e.column=col;e.sequence=sequence_;e.test_flag=flag;
  e.name=named(text[3])?text[3]:Json();
  if(!(flag&0x7d))e.result=(flag&0x80)?0:1;
  e.valid=e.result.has_value();e.effective=std::make_shared<const Json>(Json::Object{});
  context(head,site).events.push_back(std::move(e));
  if(!catalog_.count(col)) {catalog_[col]=Json::Object{{"column",col},{"test_num",number},
    {"name",named(text[3])?text[3]:Json()},{"record_type","FTR"}};column_order_.push_back(col);}
}
void Parser::part(Reader& r) {
  int head=r.u1(),site=r.u1(),flag=r.u1();r.u2();auto hard=r.u2();
  Json soft=r.optional_int(2),x=r.left()?Json(r.i2()):Json(),y=r.left()?Json(r.i2()):Json(),time=r.optional_int(4);
  auto name=r.cn(),text=r.cn();if(r.left()){auto n=r.u1();r.skip(n);}
  auto& p=context(head,site);auto& v=p.fields;
  v["__source_record_sequence"]=sequence_;v["__conditions_json"]=metadata_.get("mir").dump();
  v["Name"]=name.empty()?lot_+"_"+wafer_+"_"+(x.null()?"None":x.dump())+"_"+(y.null()?"None":y.dump())+
    "_"+std::to_string(site)+"_"+v["__part_index"].dump():name;
  v["STDF_PART_ID"]=name;v["PART_TXT"]=text;v["HBIN"]=hard;
  v["SBIN"]=soft.integer()==65535?Json():soft;v["DIE_X"]=x.integer()==-32768?Json():x;
  v["DIE_Y"]=y.integer()==-32768?Json():y;v["TEST_TIME"]=time;v["PART_FLAG"]=flag;
  v["PASS_FAIL"]=(flag&4)?"ABORT":(flag&16)?"UNKNOWN":(flag&8)?"FAIL":"PASS";
  Json::Array missing;for(auto f:{"SBIN","DIE_X","DIE_Y","TEST_TIME"})if(v.get(f).null())missing.emplace_back(f);
  v["__missing_fields"]=missing;
  parts_.push_back(std::move(p));contexts_.erase({head,site});
}
void Parser::summary(Reader& r) {
  if(r.left()<7)throw std::runtime_error("TSR 必需字段不完整");
  r.u1();r.u1();auto type=r.c1();auto number=r.u4();
  for(int i=0;i<3;++i)if(r.left())r.u4();
  auto name=r.text();r.text();r.text();if(r.left())r.u1();
  for(int i=0;i<5;++i)if(r.left())r.r4();
  if(r.left())throw std::runtime_error("TSR 存在无法解释的额外字段");
  auto kind=type=="P"?"PTR":type=="M"?"MPR":type=="F"?"FTR":"";
  if(named(name)) {
    if(std::string(kind).empty())warning("TSR 测试项 "+std::to_string(number)+" 类型未知，未回填名称。");
    else summaries_[{kind,number}].insert(name.text());
  }
}
void Parser::record(int type,int sub,const std::vector<uint8_t>& payload) {
  ++sequence_;
  static const std::map<std::pair<int,int>,std::string> names={
    {{0,10},"FAR"},{{0,20},"ATR"},{{1,10},"MIR"},{{1,20},"MRR"},{{2,10},"WIR"},
    {{2,20},"WRR"},{{5,10},"PIR"},{{5,20},"PRR"},{{15,10},"PTR"},{{15,15},"MPR"},
    {{15,20},"FTR"},{{10,30},"TSR"}};
  auto found=names.find({type,sub});
  auto kind=found==names.end()?std::to_string(type)+":"+std::to_string(sub):found->second;
  auto& counts=metadata_["record_counts"];counts[kind]=counts.get(kind).integer()+1;
  Reader r{payload,little};
  if(kind=="PTR"||kind=="MPR")parametric(r,kind);
  else if(kind=="FTR")functional(r);
  else if(kind=="PRR")part(r);
  else if(kind=="TSR")summary(r);
  else if(kind=="PIR") {
    if(r.left()!=2)throw std::runtime_error("PIR 必需字段不完整");
    int head=r.u1(),site=r.u1();
    if(contexts_.count({head,site}))throw std::runtime_error("前一颗芯片缺少 PRR，不能覆盖记录");
    context(head,site);
  } else if(kind!="ATR")metadata(r,kind);
}
void Parser::finish() {
  if(!contexts_.empty())throw std::runtime_error("STDF 存在未结束的芯片测试（缺少 PRR），未保存");
  if(parts_.empty())throw std::runtime_error("STDF 没有可保存的芯片记录");
  for(auto& [key,names]:summaries_)if(names.size()>1)
    warning("TSR 测试项 "+std::to_string(key.second)+"（"+key.first+"）名称不一致，未使用汇总名称。");
  for(auto& [key,id]:identities_) {
    auto names=summaries_.find(key);
    if(!named(Json(id.name))&&names!=summaries_.end()&&names->second.size()==1)id.name=*names->second.begin();
  }
  for(auto& [col,test]:catalog_) {
    auto& id=identities_.at({test["record_type"].text(),test["test_num"].integer()});
    if(id.kind!="FTR")test["name"]=named(Json(id.name))?Json(id.name):Json();
    else if(!named(test["name"])) {
      auto names=summaries_.find({"FTR",id.number});
      if(names!=summaries_.end()&&names->second.size()==1)test["name"]=*names->second.begin();
    }
  }
  for(auto& p:parts_)for(auto& event:p.events) {
    if(event.identity->kind!="FTR"||named(event.name))continue;
    auto names=summaries_.find({"FTR",event.identity->number});
    if(names!=summaries_.end()&&names->second.size()==1)event.name=*names->second.begin();
  }
}
}  // namespace deepyield_stdf::release
