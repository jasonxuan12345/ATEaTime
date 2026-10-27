"""Registered Cgm Excel adapter, matching the existing CPK export interface."""
from deepyield.compute.release_cgm_excel_input import export_release_cgm_xlsx
from deepyield.skills.base import BaseSkill, SkillInput, SkillOutput
from deepyield.skills.registry import register_skill


class ReleaseCgmExcelSkill(BaseSkill):
    name = "release_cgm_excel"
    description = "Export the fixed single-sheet Cgm Excel from Release JSON or Parquet."
    category = "reporting"
    tags = ("release", "excel", "public")
    input_schema = {"file_paths": "list[str] (one Release JSON or Parquet)", "params": {
        "output_dir": "str (required)", "device_id": "str | None",
        "part_indices": "list[int] | None", "sites": "list[int] | None",
        "test_columns": "list[str] | None", "minimum_samples_per_site": "int | None",
        "rule_dir": "str | None", "bin1_cpk_report_path": "str | None"}}
    output_schema = {"data": {"sheet_rows": "dict[str, int]"}, "artifacts": {"xlsx": "str"}}

    def run(self, input: SkillInput) -> SkillOutput:
        try:
            if len(input.file_paths) != 1:
                raise ValueError("请提供一份 Release JSON 或 Release Parquet")
            if set(input.params) - set(self.input_schema["params"]):
                raise ValueError(f"不支持的参数：{sorted(set(input.params) - set(self.input_schema['params']))}")
            if not input.params.get("output_dir"):
                raise ValueError("请指定 output_dir")
            output = export_release_cgm_xlsx(input.file_paths[0], **input.params)
            return self.success_output(data={"sheet_rows": output["sheet_rows"]},
                artifacts={"xlsx": output["path"]}, warnings=output["warnings"],
                summary="已生成 raw Sheet 的 Cgm Excel。",
                metadata={"source": output["source"], "rules": output["rules"]})
        except Exception as exc:
            return self.error_output(error=exc, summary="Cgm Excel 导出失败。")


register_skill(ReleaseCgmExcelSkill())
