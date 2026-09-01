from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent.skill.model import Skill
from agent.skill.prompt import format_skills_for_prompt

DEFAULT_README_PATH: str | None = None
DEFAULT_DOCS_PATH: str | None = None
DEFAULT_EXAMPLES_PATH: str | None = None


@dataclass(frozen=True)
class BuildSystemPromptOptions:
    custom_prompt: str | None = None
    selected_tools: list[str] | None = None
    tool_snippets: dict[str, str] | None = None
    prompt_guidelines: list[str] | None = None
    append_system_prompt: str | None = None
    cwd: str = ""
    context_files: list[dict[str, str]] | None = None
    skills: Sequence[Skill] | None = None
    harness_name: str = "Logi"


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    if not options.cwd:
        raise ValueError("BuildSystemPromptOptions.cwd must not be empty")
    for i, cf in enumerate(options.context_files or ()):
        if "path" not in cf or "content" not in cf:
            raise ValueError(
                f"context_files[{i}] must have 'path' and 'content' keys"
            )

    cwd_forward_slashed = options.cwd.replace("\\", "/")

    if options.custom_prompt is not None:
        # Short-circuit path: only the chosen text + optional sections + cwd.
        parts: list[str] = [options.custom_prompt]
        if options.append_system_prompt:
            parts.append(options.append_system_prompt)
        parts.extend(_project_context_section(options.context_files))
        parts.append(
            _skills_section(options.skills, options.selected_tools)
        )
        parts.append(f"Current working directory: {cwd_forward_slashed}")
        return _join(parts)
    parts = [
        (
            f"You are an expert coding assistant operating inside "
            f"{options.harness_name}, a coding agent harness. You help users by "
            f"reading files, executing commands, editing code, and writing new files."
        ),
        _tools_section(options.selected_tools, options.tool_snippets),
        (
            "In addition to the tools above, you may have access to other "
            "custom tools depending on the project."
        ),
        _guidelines_section(options.selected_tools, options.prompt_guidelines),
        _append_section(options.append_system_prompt),
    ]
    parts.extend(_project_context_section(options.context_files))
    parts.append(_skills_section(options.skills, options.selected_tools))
    parts.append(f"Current working directory: {cwd_forward_slashed}")
    return _join(parts)


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p)


def _project_context_section(
    context_files: list[dict[str, str]] | None,
) -> list[str]:
    if not context_files:
        return []
    lines = ["<project_context>", ""]
    lines.append("Project-specific instructions and guidelines:")
    lines.append("")
    #项目中特定文件说明，放到system里面，灵活为不同项目注入不同规则
    for cf in context_files:
        lines.append(f'<project_instructions path="{cf["path"]}">')
        lines.append(cf["content"])
        lines.append("</project_instructions>")
        lines.append("")
    lines.append("</project_context>")
    return ["\n".join(lines)]


def _skills_section(
    skills: Sequence[Skill] | None,
    selected_tools: list[str] | None,
) -> str:
    if not skills:
        return ""
    #这里因为要读取技能，如果没有read 技能，skill将无法做渐进式披露
    has_read = (selected_tools is None) or ("read" in selected_tools)
    if not has_read:
        return ""
    
    return format_skills_for_prompt(skills).lstrip("\n")


def _tools_section(
    selected_tools: list[str] | None,
    tool_snippets: dict[str, str] | None,
) -> str:
    snippets = tool_snippets or {}
    if selected_tools is None:
        tools = ["read", "bash", "edit", "write"]
    else:
        tools = list(selected_tools)
    visible = [name for name in tools if name in snippets]
    if not visible:
        return "Available tools:\n(none)"
    #工具展示格式 工具名:工具描述
    lines = [f"- {name}: {snippets[name]}" for name in visible]
    return "Available tools:\n" + "\n".join(lines)


def _guidelines_section(
    selected_tools: list[str] | None,
    prompt_guidelines: list[str] | None,
) -> str:
    tools_set = set(selected_tools) if selected_tools is not None else set(
        ["read", "bash", "edit", "write"]
    )
    guidelines: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        if line and line not in seen:
            seen.add(line)
            guidelines.append(line)

    if "bash" in tools_set and not ({"grep", "find", "ls"} & tools_set):
        add("Use bash for file operations like ls, rg, find")
    for raw in prompt_guidelines or ():
        add(raw.strip())
    add("Be concise in your responses")
    add("Show file paths clearly when working with files")
    body = "\n".join(f"- {g}" for g in guidelines)
    return f"Guidelines:\n{body}"

def _append_section(append: str | None) -> str:
    """Return the append text or empty string."""
    return append or ""
