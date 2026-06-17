from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query

from ..models import TemplateCreateRequest, TemplateUpdateRequest
from ..core.template import template_manager, ScriptTemplate

router = APIRouter(prefix="/templates", tags=["Templates"])


def _template_to_response(t: ScriptTemplate) -> dict:
    return t.to_dict()


@router.get("")
async def list_templates(tag: Optional[str] = Query(None), keyword: Optional[str] = Query(None)):
    templates = template_manager.list_templates(tag=tag, keyword=keyword)
    return [_template_to_response(t) for t in templates]


@router.get("/tags")
async def list_tags():
    return template_manager.get_all_tags()


@router.get("/{template_id}")
async def get_template(template_id: str):
    t = template_manager.get_template(template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_to_response(t)


@router.post("", status_code=201)
async def create_template(req: TemplateCreateRequest):
    t = template_manager.create_template(
        name=req.name,
        script_content=req.script_content,
        description=req.description or "",
        interpreter=req.interpreter,
        tags=req.tags,
    )
    return _template_to_response(t)


@router.put("/{template_id}")
async def update_template(template_id: str, req: TemplateUpdateRequest):
    update_kwargs = req.model_dump(exclude_unset=True)
    t = template_manager.update_template(template_id, **update_kwargs)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_to_response(t)


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    if not template_manager.delete_template(template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"message": "Template deleted successfully"}
