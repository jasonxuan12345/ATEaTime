"""Central registry for AI-callable DeepYield skills."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from deepyield.skills.base import BaseSkill, STANDARD_SKILL_OUTPUT_SCHEMA, SkillInput, SkillOutput


_SKILL_REGISTRY: dict[str, BaseSkill] = {}
_SKILL_REGISTRY_PRIORITIES: dict[str, int] = {}


def _normalize_input(input: SkillInput | Mapping[str, Any]) -> SkillInput:
    """Return a SkillInput instance from an existing instance or mapping."""
    if isinstance(input, SkillInput):
        return input
    if isinstance(input, Mapping):
        return SkillInput(**dict(input))
    raise TypeError("input must be a SkillInput or a mapping compatible with SkillInput.")


def _skill_priority(skill: BaseSkill) -> int:
    """Return the registry priority declared by a skill."""
    priority = getattr(skill, "registry_priority", 0)
    if not isinstance(priority, int):
        raise TypeError(f"Skill '{skill.name}' registry_priority must be an integer.")
    return priority


def _same_skill_implementation(left: BaseSkill, right: BaseSkill) -> bool:
    """Return whether two skill instances come from the same implementation."""
    left_type = type(left)
    right_type = type(right)
    return left_type.__module__ == right_type.__module__ and left_type.__qualname__ == right_type.__qualname__


def register_skill(skill: BaseSkill) -> BaseSkill:
    """Register one skill instance by name.

    If another skill already uses the same name, the higher
    ``registry_priority`` wins. This makes product-level wrappers stable
    regardless of module import order while still surfacing accidental
    same-priority duplicate names.

    Raises:
        TypeError: If ``skill`` is not a BaseSkill instance.
        ValueError: If another different skill with the same priority already
            uses the same name.
    """
    if not isinstance(skill, BaseSkill):
        raise TypeError("skill must be an instance of BaseSkill.")

    name = skill.name.strip()
    if not name:
        raise ValueError("skill.name must be a non-empty string.")
    priority = _skill_priority(skill)
    if name in _SKILL_REGISTRY:
        current = _SKILL_REGISTRY[name]
        current_priority = _SKILL_REGISTRY_PRIORITIES.get(name, _skill_priority(current))
        if priority > current_priority:
            _SKILL_REGISTRY[name] = skill
            _SKILL_REGISTRY_PRIORITIES[name] = priority
            return skill
        if priority < current_priority:
            return current
        if _same_skill_implementation(current, skill):
            _SKILL_REGISTRY[name] = skill
            _SKILL_REGISTRY_PRIORITIES[name] = priority
            return skill
        raise ValueError(f"Skill already registered with the same priority: {name}")

    _SKILL_REGISTRY[name] = skill
    _SKILL_REGISTRY_PRIORITIES[name] = priority
    return skill


def get_skill(name: str) -> BaseSkill:
    """Return a registered skill by name."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string.")

    normalized_name = name.strip()
    try:
        return _SKILL_REGISTRY[normalized_name]
    except KeyError as exc:
        raise KeyError(f"Skill not found: {normalized_name}") from exc


def _normalized_tags(skill: BaseSkill) -> list[str]:
    tags = getattr(skill, "tags", ())
    if tags is None:
        return []
    if isinstance(tags, str):
        return [tags]
    return [str(tag) for tag in tags]


def _output_schema(skill: BaseSkill) -> dict[str, Any]:
    schema = deepcopy(STANDARD_SKILL_OUTPUT_SCHEMA)
    schema.update(deepcopy(getattr(skill, "output_schema", {}) or {}))
    return schema


def _describe_registered_skill(skill: BaseSkill) -> dict[str, Any]:
    """Return catalog metadata for a registered skill."""
    return {
        "name": skill.name,
        "description": skill.description,
        "category": str(getattr(skill, "category", "general") or "general"),
        "tags": _normalized_tags(skill),
        "registry_priority": _SKILL_REGISTRY_PRIORITIES.get(skill.name, _skill_priority(skill)),
        "input_schema": deepcopy(getattr(skill, "input_schema", {}) or {}),
        "output_schema": _output_schema(skill),
        "implementation": f"{type(skill).__module__}.{type(skill).__qualname__}",
    }


def describe_skill(name: str) -> dict[str, Any]:
    """Return catalog metadata for one registered skill."""
    return _describe_registered_skill(get_skill(name))


def list_skills() -> list[dict[str, Any]]:
    """List registered skill metadata for agents and UIs."""
    return [_describe_registered_skill(skill) for skill in sorted(_SKILL_REGISTRY.values(), key=lambda item: item.name)]


def run_skill(name: str, input: SkillInput | Mapping[str, Any]) -> SkillOutput:
    """Run a registered skill and return a standardized output.

    Skill implementations should catch expected validation/compute errors
    themselves. This function still catches unexpected exceptions so agent
    callers always receive a SkillOutput.
    """
    try:
        skill = get_skill(name)
    except Exception as exc:
        return SkillOutput(
            success=False,
            data=None,
            summary=f"Skill '{name}' failed.",
            artifacts={},
            warnings=[],
            errors=[str(exc)],
            metadata={"skill": name, "error_type": type(exc).__name__},
        )

    try:
        normalized_input = _normalize_input(input)
        output = skill.run(normalized_input)
        if not isinstance(output, SkillOutput):
            raise TypeError(f"Skill '{skill.name}' returned {type(output).__name__}, expected SkillOutput.")
        return output
    except Exception as exc:
        return skill.error_output(
            error=exc,
            summary=f"Skill '{skill.name}' failed.",
            metadata={"error_type": type(exc).__name__},
        )
