"""agent.skill — skill loading and prompt formatting.

Public API:
 Skill — dataclass
 SkillSourceInfo — dataclass
 load_skills — multi-directory loader
 load_skill_from_file — single-file loader (exported for tests)
 format_skills_for_prompt — XML prompt block
"""
from agent.skill.frontmatter import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    parse_frontmatter,
    validate_description,
    validate_name,
)
from agent.skill.loader import (
    load_skill_from_file,
    load_skills,
    load_skills_from_dir,
)
from agent.skill.model import Skill, SkillSourceInfo
from agent.skill.prompt import format_skills_for_prompt

__all__ = [
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "Skill",
    "SkillSourceInfo",
    "format_skills_for_prompt",
    "load_skill_from_file",
    "load_skills",
    "load_skills_from_dir",
    "parse_frontmatter",
    "validate_description",
    "validate_name",
]
