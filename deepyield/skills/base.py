"""Base contracts for AI-callable DeepYield skills.

Skills are thin orchestration wrappers. They validate caller input, call
deterministic functions from ``deepyield.compute``, and return standardized
structured outputs for agents and pipelines.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


STANDARD_SKILL_OUTPUT_SCHEMA: dict[str, Any] = {
    "success": "bool",
    "summary": "str",
    "data": "Any",
    "artifacts": "dict[str, Any]",
    "warnings": "list[str]",
    "errors": "list[str]",
    "metadata": "dict[str, Any]",
}


def _serialize_for_display(value: Any) -> Any:
    """Convert common analysis objects into UI/API-friendly values."""
    if isinstance(value, pd.DataFrame):
        return {
            "type": "dataframe",
            "row_count": int(len(value)),
            "column_count": int(len(value.columns)),
            "columns": [str(column) for column in value.columns],
            "records": value.to_dict("records"),
        }
    if isinstance(value, pd.Series):
        return value.to_list()
    if isinstance(value, bytes):
        return {"type": "bytes", "byte_count": len(value)}
    if isinstance(value, dict):
        return {str(key): _serialize_for_display(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_for_display(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


@dataclass
class SkillInput:
    """Standard input payload accepted by AI-callable skills."""

    dataframe: pd.DataFrame | None = None
    dataframes: dict[str, pd.DataFrame] | None = None
    file_paths: list[str] | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize optional containers and validate obvious type errors."""
        if self.dataframe is not None and not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError("SkillInput.dataframe must be a pandas DataFrame or None.")

        if self.dataframes is None:
            self.dataframes = {}
        elif not isinstance(self.dataframes, dict):
            raise TypeError("SkillInput.dataframes must be a dict[str, pandas DataFrame] or None.")
        else:
            invalid = [name for name, frame in self.dataframes.items() if not isinstance(frame, pd.DataFrame)]
            if invalid:
                raise TypeError(f"SkillInput.dataframes contains non-DataFrame value(s): {', '.join(invalid)}.")

        if self.file_paths is None:
            self.file_paths = []
        elif not isinstance(self.file_paths, list) or not all(isinstance(path, str) for path in self.file_paths):
            raise TypeError("SkillInput.file_paths must be a list[str] or None.")

        if self.params is None:
            self.params = {}
        elif not isinstance(self.params, dict):
            raise TypeError("SkillInput.params must be a dict[str, Any].")


@dataclass
class SkillOutput:
    """Standard structured output returned by every skill."""

    success: bool
    data: Any = None
    summary: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, serialize: bool = False) -> dict[str, Any]:
        """Return the stable public output shape.

        ``serialize=True`` converts pandas objects and bytes into values that
        are easier for UI/API catalog pages to display.
        """
        data = _serialize_for_display(self.data) if serialize else self.data
        artifacts = _serialize_for_display(self.artifacts) if serialize else self.artifacts
        return {
            "success": self.success,
            "summary": self.summary,
            "data": data,
            "artifacts": artifacts,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }


class BaseSkill(ABC):
    """Abstract base class for deterministic compute wrappers."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}
    category: str = "general"
    tags: tuple[str, ...] = ()
    registry_priority: int = 0

    def __init__(self) -> None:
        """Validate required skill metadata at construction time."""
        if not self.name:
            raise ValueError("Skill name must be a non-empty string.")
        if not self.description:
            raise ValueError(f"Skill '{self.name}' must define a description.")

    @abstractmethod
    def run(self, input: SkillInput) -> SkillOutput:
        """Execute the skill and return a standardized output."""

    def success_output(
        self,
        *,
        data: Any = None,
        summary: str = "",
        artifacts: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SkillOutput:
        """Build a successful output payload."""
        return SkillOutput(
            success=True,
            data=data,
            summary=summary,
            artifacts=artifacts or {},
            warnings=warnings or [],
            errors=[],
            metadata={"skill": self.name, **(metadata or {})},
        )

    def error_output(
        self,
        *,
        error: Exception | str,
        summary: str = "",
        data: Any = None,
        artifacts: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SkillOutput:
        """Build a failed output payload without leaking stack traces by default."""
        message = str(error)
        return SkillOutput(
            success=False,
            data=data,
            summary=summary or message,
            artifacts=artifacts or {},
            warnings=warnings or [],
            errors=[message],
            metadata={"skill": self.name, **(metadata or {})},
        )
