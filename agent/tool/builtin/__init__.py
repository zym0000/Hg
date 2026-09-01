from __future__ import annotations

from typing import Any

from agent.tool.agent_tool import AgentTool
from agent.tool.builtin.bash import create_bash_tool
from agent.tool.builtin.edit import create_edit_tool
from agent.tool.builtin.find import create_find_tool
from agent.tool.builtin.grep import create_grep_tool
from agent.tool.builtin.ls import create_ls_tool
from agent.tool.builtin.read import create_read_tool
from agent.tool.builtin.write import create_write_tool


def create_coding_tools(cwd: str, options: Any = None) -> list[AgentTool]:
    """[read, bash, edit, write]"""
    opts = options or {}
    return [
        create_read_tool(cwd, opts.get("read")),
        create_bash_tool(cwd, opts.get("bash")),
        create_edit_tool(cwd, opts.get("edit")),
        create_write_tool(cwd, opts.get("write")),
    ]


def create_read_only_tools(cwd: str, options: Any = None) -> list[AgentTool]:
    """[read, grep, find, ls]"""
    opts = options or {}
    return [
        create_read_tool(cwd, opts.get("read")),
        create_grep_tool(cwd, opts.get("grep")),
        create_find_tool(cwd, opts.get("find")),
        create_ls_tool(cwd, opts.get("ls")),
    ]

def create_all_tools(cwd: str, options: Any = None) -> dict[str, AgentTool]:
    """{read, bash, edit, write, grep, find, ls}"""
    opts = options or {}
    return {
        "read": create_read_tool(cwd, opts.get("read")),
        "bash": create_bash_tool(cwd, opts.get("bash")),
        "edit": create_edit_tool(cwd, opts.get("edit")),
        "write": create_write_tool(cwd, opts.get("write")),
        "grep": create_grep_tool(cwd, opts.get("grep")),
        "find": create_find_tool(cwd, opts.get("find")),
        "ls": create_ls_tool(cwd, opts.get("ls")),
    }

__all__ = [
    "create_coding_tools",
    "create_read_only_tools",
    "create_all_tools",
    "create_read_tool",
    "create_bash_tool",
    "create_edit_tool",
    "create_write_tool",
    "create_ls_tool",
    "create_find_tool",
    "create_grep_tool",
]