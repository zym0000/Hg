from __future__ import annotations

import re
from typing import Any

import yaml

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

_NAME_RE = re.compile(r"^[a-z0-9-]+$")


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    body = text[3:]
    end = body.find("\n---")
    if end == -1:
        return {}
    yaml_text = body[:end]
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid frontmatter YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        return {}
    return {str(k): v for k, v in parsed.items()}


def validate_name(raw: str | None) -> str:
    if raw is None or not isinstance(raw, str):
        raise ValueError(f"invalid skill name: {raw!r}")
    if not raw:
        raise ValueError("skill name must not be empty")
    if len(raw) > MAX_NAME_LENGTH:
        raise ValueError(
            f"skill name {raw!r} too long ({len(raw)} > {MAX_NAME_LENGTH})"
        )
    #只能包含小写字母（a-z）、数字（0-9）和连字符（-），用来解决文件名导致一些不安全问题：
    #防 XML/HTML 破坏
    #跨平台文件系统兼容性
    #它保证了无论技能被用于文件系统、Shell、URL 还是 LLM 提示词，都不会产生任何副作用、转义或解析错误
    if not _NAME_RE.match(raw):
        raise ValueError(
            f"skill name {raw!r} must match /^[a-z0-9-]+$/ "
            f"(lowercase alnum and hyphens only)"
        )
    if raw.startswith("-") or raw.endswith("-"):
        raise ValueError(
            f"skill name {raw!r} must not start or end with a hyphen"
        )
    if "--" in raw:
        raise ValueError(f"skill name {raw!r} must not contain '--'")
    return raw


def validate_description(raw: str | None) -> str:
    if raw is None or not isinstance(raw, str):
        raise ValueError("skill description must be a non-empty string")
    text = raw.strip()
    if not text:
        raise ValueError("skill description must be a non-empty string")
    #防止skill描述过长
    if len(text) > MAX_DESCRIPTION_LENGTH:
        return text[:MAX_DESCRIPTION_LENGTH]
    return text
