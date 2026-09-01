from dataclasses import dataclass

@dataclass(frozen=True)
class SkillSourceInfo:
    type: str
    path: str

#skill元数据信息，用于渐进式披露
@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    file_path: str
    base_dir: str
    source_info: SkillSourceInfo
    disable_model_invocation: bool = False #skill 是否启用
