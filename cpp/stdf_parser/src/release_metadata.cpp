#include "deepyield_stdf/release_import.hpp"
namespace deepyield_stdf::release {
void Parser::metadata(Reader& r,const std::string& kind) {
  const std::map<std::string,int> required={{"FAR",2},{"MIR",15},{"WIR",6},{"WRR",10},{"MRR",4}};
  auto size=required.find(kind);
  if(size!=required.end()&&r.left()<size_t(size->second))throw std::runtime_error(kind+" 必需字段不完整");
  Json v=Json::Object{};
  if(kind=="FAR") {
    v["cpu_type"]=r.u1();v["stdf_version"]=r.u1();metadata_["far"]=v;
  } else if(kind=="MIR") {
    v["setup_time"]=r.u4();v["start_time"]=r.u4();v["station_number"]=r.u1();
    v["mode_code"]=r.c1();v["retest_code"]=r.c1();v["protection_code"]=r.c1();
    v["burn_time"]=r.u2();v["command_mode_code"]=r.c1();
    for(auto key:{"lot_id","part_type","node_name","tester_type","job_name","job_revision",
        "sublot_id","operator_name","exec_type","exec_version","test_code","test_temperature",
        "user_text","aux_file","package_type","family_id","date_code","facility_id","floor_id",
        "process_id","operation_frequency","spec_name","spec_version","flow_id","setup_id",
        "design_revision","engineer_id","rom_code","serial_number","supervisor_name"})v[key]=r.cn();
    lot_=v.get("lot_id").text();metadata_["mir"]=v;
  } else if(kind=="MRR") {
    v["finish_time"]=r.u4();v["disposition_code"]=r.c1();
    v["user_description"]=r.cn();v["exec_description"]=r.cn();metadata_["mrr"]=v;
  } else if(kind=="WIR") {
    v["head_number"]=r.u1();v["site_group"]=r.u1();v["start_time"]=r.u4();v["wafer_id"]=r.cn();
    if(!v.get("wafer_id").text().empty())wafer_=v.get("wafer_id").text();
    metadata_["wafers"].array().push_back(v);
  } else if(kind=="WRR") {
    v["head_number"]=r.u1();v["site_group"]=r.u1();v["finish_time"]=r.u4();
    for(auto key:{"part_count","retest_count","abort_count","good_count","functional_count"})v[key]=r.optional_int(4);
    for(auto key:{"wafer_id","fab_wafer_id","frame_id","mask_id","user_description","exec_description"})v[key]=r.cn();
    metadata_["wrr"].array().push_back(v);
  }
}
}  // namespace deepyield_stdf::release
