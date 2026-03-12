"""Content profile inference and signal requirement helpers."""

from __future__ import annotations

from typing import Any


PROFILE_DEFINITIONS: dict[str, dict[str, object]] = {
    "skill_recommendation": {
        "required_signal_keys": ["projects", "links", "skills", "skill_ids", "commands"],
        "optional_signal_keys": ["use_cases", "validation_actions"],
        "require_action_checklist": True,
        "require_project_section": True,
    },
    "installation_tutorial": {
        "required_signal_keys": ["commands"],
        "optional_signal_keys": ["prerequisites", "validation_actions", "common_errors"],
        "require_action_checklist": True,
        "require_project_section": True,
    },
    "project_overview": {
        "required_signal_keys": ["projects", "links"],
        "optional_signal_keys": ["purposes", "boundaries"],
        "require_action_checklist": False,
        "require_project_section": True,
    },
    "video_explainer": {
        "required_signal_keys": ["links"],
        "optional_signal_keys": ["purposes"],
        "require_action_checklist": False,
        "require_project_section": False,
    },
    "general_capture": {
        "required_signal_keys": [],
        "optional_signal_keys": [],
        "require_action_checklist": False,
        "require_project_section": False,
    },
}


def infer_content_profile(
    source_kind: str,
    source_url: str | None,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    meta = metadata if isinstance(metadata, dict) else {}
    signals = meta.get("signals", {}) if isinstance(meta.get("signals"), dict) else {}
    steps = meta.get("steps", []) if isinstance(meta.get("steps"), list) else []
    step_items = meta.get("step_items", []) if isinstance(meta.get("step_items"), list) else []
    lowered_text = (text or "").lower()
    lowered_url = (source_url or "").lower()

    has_skill = bool(signals.get("skills") or signals.get("skill_ids")) or any(
        token in lowered_text for token in ["/install-skill", ".skill", " skill", "技能id", "skill id"]
    )
    has_install = bool(signals.get("commands") or steps or step_items) or any(
        token in lowered_text for token in ["安装", "教程", "步骤", "配置", "setup", "install", "前置条件"]
    )
    has_project = bool(signals.get("projects")) or "github.com/" in lowered_url or any(
        token in lowered_text for token in ["项目", "仓库", "repo", "repository", "readme"]
    )

    if source_kind == "video_url":
        kind = "video_explainer"
    elif has_skill:
        kind = "skill_recommendation"
    elif has_install:
        kind = "installation_tutorial"
    elif has_project:
        kind = "project_overview"
    else:
        kind = "general_capture"

    definition = PROFILE_DEFINITIONS.get(kind, PROFILE_DEFINITIONS["general_capture"])
    return {
        "kind": kind,
        "required_signal_keys": list(definition["required_signal_keys"]),
        "optional_signal_keys": list(definition["optional_signal_keys"]),
        "require_action_checklist": bool(definition["require_action_checklist"]),
        "require_project_section": bool(definition["require_project_section"]),
    }


def iter_required_signal_entries(
    content_profile: dict[str, object] | None,
    signals: dict[str, list[str]] | None,
) -> list[tuple[str, str]]:
    profile = content_profile if isinstance(content_profile, dict) else {}
    signal_map = signals if isinstance(signals, dict) else {}
    entries: list[tuple[str, str]] = []
    for key in profile.get("required_signal_keys", []):
        values = signal_map.get(str(key), [])
        if isinstance(values, list):
            selected = list(values)
            if str(key) == "links":
                preferred = [
                    item
                    for item in values
                    if "/raw/" not in str(item).lower() and not str(item).lower().endswith(".skill")
                ]
                if preferred:
                    selected = preferred
            for item in selected[:1]:
                token = str(item).strip()
                if token:
                    entries.append((str(key), token))
    return entries


def build_signal_requirements(
    content_profile: dict[str, object] | None,
    signals: dict[str, list[str]] | None,
) -> dict[str, object]:
    profile = content_profile if isinstance(content_profile, dict) else {}
    signal_map = signals if isinstance(signals, dict) else {}
    return {
        "kind": str(profile.get("kind", "general_capture")),
        "required_signal_keys": [str(item) for item in profile.get("required_signal_keys", [])],
        "optional_signal_keys": [str(item) for item in profile.get("optional_signal_keys", [])],
        "required_signal_values": [
            {"key": key, "value": value}
            for key, value in iter_required_signal_entries(profile, signal_map)
        ],
        "require_action_checklist": bool(profile.get("require_action_checklist", False)),
        "require_project_section": bool(profile.get("require_project_section", False)),
    }
