"""Registered single-Parquet Release CGM skill."""
from deepyield.compute.release_cgm import analyze_release_cgm
from deepyield.skills.base import BaseSkill, SkillInput, SkillOutput
from deepyield.skills.registry import register_skill


class ReleaseCgmSkill(BaseSkill):
    name = "release_cgm"
    description = "Compute selected Release site Cg/min_Cg with matching BIN1 CPK rules."
    category = "capability"
    tags = ("release", "parquet", "public")
    input_schema = {"file_paths": "list[str] (one Release Parquet)", "params": {
        "part_indices": "list[int] | None", "sites": "list[int] | None", "test_columns": "list[str] | None",
        "minimum_samples_per_site": "int | None", "bin1_cpk_report_path": "str | None", "rule_dir": "str | None"}}
    output_schema = {"data": {"report": "dict"}}

    def run(self, input: SkillInput) -> SkillOutput:
        try:
            if len(input.file_paths) != 1:
                raise ValueError("请提供一份 Release Parquet 文件")
            allowed = set(self.input_schema["params"])
            if set(input.params) - allowed:
                raise ValueError(f"不支持的参数：{sorted(set(input.params) - allowed)}")
            report = analyze_release_cgm(input.file_paths[0], **input.params)
            return self.success_output(data={"report": report}, warnings=report["warnings"],
                summary=f"已检查 {len(report['items'])} 个 CGM 测项分组，最终 Release 由工程师确认。",
                metadata={"source": report["source"], "rules": report["rules"]})
        except Exception as exc:
            return self.error_output(error=exc, summary="CGM 检查失败。")


register_skill(ReleaseCgmSkill())
