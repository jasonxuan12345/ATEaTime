"""Thin, registered skill for the release single-Parquet BIN1 report."""
from deepyield.compute.release_cpk import analyze_release_bin1_cpk
from deepyield.skills.base import BaseSkill, SkillInput, SkillOutput
from deepyield.skills.registry import register_skill


class ReleaseBin1CpkSkill(BaseSkill):
    name = "release_bin1_cpk"
    description = "Compute selected release Parquet BIN1 CPK and classify with configured rules."
    category = "capability"
    tags = ("release", "parquet", "public")
    input_schema = {"file_paths": "list[str] (one Release Parquet)", "params": {
        "part_indices": "list[int] | None", "sites": "list[int] | None",
        "test_columns": "list[str] | None", "minimum_samples": "int | None",
        "rule_dir": "str | None"}}
    output_schema = {"data": {"report": "dict"}}

    def run(self, input: SkillInput) -> SkillOutput:
        try:
            if len(input.file_paths) != 1:
                raise ValueError("请提供一份 Release Parquet 文件")
            allowed = {"part_indices", "sites", "test_columns", "minimum_samples", "rule_dir"}
            if set(input.params) - allowed:
                raise ValueError(f"不支持的参数：{sorted(set(input.params) - allowed)}")
            report = analyze_release_bin1_cpk(input.file_paths[0], **input.params)
            return self.success_output(data={"report": report}, warnings=report["warnings"],
                summary=f"已检查 {len(report['items'])} 个测项分组，最终 Release 由工程师确认。",
                metadata={"source": report["source"], "rules": report["rules"]})
        except Exception as exc:
            return self.error_output(error=exc, summary="bin1 CPK 检查失败。")


register_skill(ReleaseBin1CpkSkill())
