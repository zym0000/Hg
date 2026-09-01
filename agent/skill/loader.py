from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from agent.skill.frontmatter import (
    parse_frontmatter,
    validate_description,
    validate_name,
)
from agent.skill.model import Skill, SkillSourceInfo


def load_skills(dirs: Sequence[Path]) -> list[Skill]:
    real_path_set: set[str] = set()
    by_name: dict[str, Skill] = {}

    for d in dirs:
        d = d.resolve()
        if not d.is_dir():
            continue
        src = SkillSourceInfo(type="path", path=str(d))
        for skill in load_skills_from_dir(d, src):
            real = str(Path(skill.file_path).resolve())
            if real in real_path_set:
                continue
            if skill.name in by_name:
                print(
                    f"[load_skills] collision: skill {skill.name!r} "
                    f"already loaded, skipping {skill.file_path}",
                    file=sys.stderr,
                )
                continue
            real_path_set.add(real)
            by_name[skill.name] = skill

    return list(by_name.values())


def load_skills_from_dir(dir_path: Path, source_info: SkillSourceInfo) -> list[Skill]:
    dir_path = dir_path.resolve()
    if not dir_path.is_dir():
        return []

    skills: list[Skill] = []

    # Top-level .md files: each is its own skill.
    for entry in sorted(dir_path.iterdir()):
        if entry.is_file() and entry.suffix == ".md" and entry.name != "SKILL.md":
            skill = _try_load(entry, dir_path, source_info)
            if skill is not None:
                skills.append(skill)
        elif entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                skill = _try_load(skill_md, entry, source_info)
                if skill is not None:
                    skills.append(skill)
            else:
                skills.extend(load_skills_from_dir(entry, source_info))

    return skills


def load_skill_from_file(
    file_path: Path,
    base_dir: Path,
    source_info: SkillSourceInfo,
) -> Skill:
    text = file_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    name = validate_name(fm.get("name"))
    description = validate_description(fm.get("description"))
    disable = bool(fm.get("disable-model-invocation", False))

    return Skill(
        name=name,
        description=description,
        file_path=str(file_path.resolve()),
        base_dir=str(base_dir.resolve()),
        source_info=source_info,
        disable_model_invocation=disable,
    )

def _try_load(
    file_path: Path,
    base_dir: Path,
    source_info: SkillSourceInfo,
) -> Skill | None:
    try:
        return load_skill_from_file(file_path, base_dir, source_info)
    except (ValueError, OSError) as exc:
        print(
            f"[load_skills] skipping {file_path}: {exc}",
            file=sys.stderr,
        )
        return None