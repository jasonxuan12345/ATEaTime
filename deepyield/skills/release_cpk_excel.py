"""Registered thin skill using the same fixed workbook exporter as the CLI."""
from deepyield.compute.release_cpk_excel_input import export_release_cpk_xlsx
from deepyield.skills.base import BaseSkill, SkillInput, SkillOutput
from deepyield.skills.registry import register_skill


class ReleaseCpkExcelSkill(BaseSkill):
    name = "release_cpk_excel"
    description = "Export the fixed four-sheet Bin1 CPK Excel from Release JSON or Parquet."
    category = "reporting"
    tags = ("release", "excel", "public")
    input_schema = {"file_paths": "list[str] (one Release JSON or Parquet)", "params": {
        "output_dir": "str (required)", "device_id": "str | None", "test_temp": "str | None",
        "part_indices": "list[int] | None", "sites": "list[int] | None",
        "test_columns": "list[str] | None", "minimum_samples": "int | None",
        "rule_dir": "str | None"}}
    output_schema = {"data": {"sheet_rows": "dict[str, int]"}, "artifacts": {"xlsx": "str"}}

    def run(self, input: SkillInput) -> SkillOutput:
        try:
            if len(input.file_paths) != 1:
                raise ValueError("请提供一份 Release JSON 或 Release Parquet")
            allowed = set(self.input_schema["params"])
            if set(input.params) - allowed:
                raise ValueError(f"不支持的参数：{sorted(set(input.params) - allowed)}")
            if not input.params.get("output_dir"):
                raise ValueError("请指定 output_dir")
            output = export_release_cpk_xlsx(input.file_paths[0], **input.params)
            return self.success_output(data={"sheet_rows": output["sheet_rows"]},
                artifacts={"xlsx": output["path"]}, warnings=output["warnings"],
                summary="已生成四个 Sheet 的 Bin1 CPK Excel。",
                metadata={"source": output["source"], "rules": output["rules"]})
        except Exception as exc:
            return self.error_output(error=exc, summary="CPK Excel 导出失败。")


register_skill(ReleaseCpkExcelSkill())
