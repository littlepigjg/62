import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict

from ..config import TEMPLATES_DIR


TEMPLATE_META_FILE = TEMPLATES_DIR / "_templates_meta.json"


@dataclass
class ScriptTemplate:
    id: str
    name: str
    description: str
    script_content: str
    interpreter: str = "bash"
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScriptTemplate":
        return cls(**data)


class TemplateManager:
    def __init__(self):
        self._templates: Dict[str, ScriptTemplate] = {}
        self._load_meta()

    def _meta_path(self) -> Path:
        return TEMPLATE_META_FILE

    def _script_path(self, template_id: str) -> Path:
        return TEMPLATES_DIR / f"{template_id}.sh"

    def _load_meta(self) -> None:
        meta_file = self._meta_path()
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                for tid, data in meta_data.items():
                    script_file = self._script_path(tid)
                    if script_file.exists():
                        with open(script_file, "r", encoding="utf-8") as sf:
                            data["script_content"] = sf.read()
                    self._templates[tid] = ScriptTemplate.from_dict(data)
            except Exception:
                self._templates = {}

    def _save_meta(self) -> None:
        meta_file = self._meta_path()
        meta_data = {}
        for tid, template in self._templates.items():
            t_dict = template.to_dict()
            del t_dict["script_content"]
            meta_data[tid] = t_dict
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)

    def _save_script(self, template: ScriptTemplate) -> None:
        script_file = self._script_path(template.id)
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(template.script_content)

    def _delete_script(self, template_id: str) -> None:
        script_file = self._script_path(template_id)
        if script_file.exists():
            script_file.unlink()

    def _generate_id(self, name: str) -> str:
        import re
        import uuid
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower())
        return f"{safe_name}_{uuid.uuid4().hex[:6]}"

    def list_templates(self, tag: Optional[str] = None, keyword: Optional[str] = None) -> List[ScriptTemplate]:
        templates = list(self._templates.values())
        if tag:
            templates = [t for t in templates if tag in t.tags]
        if keyword:
            kw = keyword.lower()
            templates = [
                t for t in templates
                if kw in t.name.lower() or kw in t.description.lower() or kw in t.script_content.lower()
            ]
        templates.sort(key=lambda t: t.updated_at, reverse=True)
        return templates

    def get_template(self, template_id: str) -> Optional[ScriptTemplate]:
        return self._templates.get(template_id)

    def create_template(
        self,
        name: str,
        script_content: str,
        description: str = "",
        interpreter: str = "bash",
        tags: Optional[List[str]] = None,
    ) -> ScriptTemplate:
        template_id = self._generate_id(name)
        now = datetime.now().isoformat()
        template = ScriptTemplate(
            id=template_id,
            name=name,
            description=description,
            script_content=script_content,
            interpreter=interpreter,
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        self._templates[template_id] = template
        self._save_script(template)
        self._save_meta()
        return template

    def update_template(
        self,
        template_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        script_content: Optional[str] = None,
        interpreter: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[ScriptTemplate]:
        template = self._templates.get(template_id)
        if not template:
            return None

        if name is not None:
            template.name = name
        if description is not None:
            template.description = description
        if script_content is not None:
            template.script_content = script_content
            self._save_script(template)
        if interpreter is not None:
            template.interpreter = interpreter
        if tags is not None:
            template.tags = tags

        template.updated_at = datetime.now().isoformat()
        self._save_meta()
        return template

    def delete_template(self, template_id: str) -> bool:
        if template_id in self._templates:
            del self._templates[template_id]
            self._delete_script(template_id)
            self._save_meta()
            return True
        return False

    def get_all_tags(self) -> List[str]:
        tags: set = set()
        for t in self._templates.values():
            tags.update(t.tags)
        return sorted(tags)


template_manager = TemplateManager()
