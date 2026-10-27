"""Registered single-Parquet Release MMSA (Site Diff) skill."""
from deepyield.compute.release_mmsa import analyze_release_mmsa
from deepyield.skills.base import BaseSkill, SkillInput, SkillOutput
from deepyield.skills.registry import register_skill


class ReleaseMmsaSkill(BaseSkill):
    name = "release_mmsa"
    description = "Diagnose Site Diff with dual S2S parameters for gate-triggered Release items."
    category = "capability"
    tags = ("release", "parquet", "public")
    input_schema = {"file_paths": "list[str] (one Release Parquet)", "params": {
        "part_indices": "list[int] | None", "sites": "list[int] | None", "test_columns": "list[str] | None",
        "minimum_samples_per_site": "int | None", "bin1_cpk_report_path": "str | None",
        "cgm_report_path": "str | None", "rule_dir": "str | None"}}
    output_schema = {"data": {"report": "dict"}}

    def run(self, input: SkillInput) -> SkillOutput:
        try:
            if len(input.file_paths) != 1:
                raise ValueError("请提供一份 Release Parquet 文件")
            allowed = set(self.input_schema["params"])
            if set(input.params) - allowed:
                raise ValueError(f"不支持的参数：{sorted(set(input.params) - allowed)}")
            report = analyze_release_mmsa(input.file_paths[0], **input.params)
            return self.success_output(data={"report": report}, warnings=report["warnings"],
                summary=f"已完成 {len(report['items'])} 个测项分组的 Site Diff 诊断，最终 Release 由工程师确认。",
                metadata={"source": report["source"], "rules": report["rules"]})
        except Exception as exc:
            return self.error_output(error=exc, summary="Site Diff 诊断失败。")


register_skill(ReleaseMmsaSkill())
