from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import settings, ServerConfig

router = APIRouter(prefix="/servers", tags=["Servers"])


class ServerCreateRequest(BaseModel):
    id: Optional[str] = None
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = ""
    private_key: Optional[str] = ""
    tags: List[str] = []


class ServerUpdateRequest(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None
    tags: Optional[List[str]] = None


@router.get("", response_model=List[ServerConfig])
async def list_servers(tag: Optional[str] = Query(None)):
    servers = settings.servers
    if tag:
        servers = [s for s in servers if tag in s.tags]
    return servers


@router.get("/tags")
async def list_tags():
    tags = set()
    for s in settings.servers:
        tags.update(s.tags)
    return sorted(tags)


@router.get("/{server_id}", response_model=ServerConfig)
async def get_server(server_id: str):
    server = settings.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.post("", response_model=ServerConfig, status_code=201)
async def create_server(req: ServerCreateRequest):
    import uuid
    sid = req.id or f"server-{uuid.uuid4().hex[:8]}"
    if settings.get_server(sid):
        raise HTTPException(status_code=400, detail=f"Server with id '{sid}' already exists")

    server = ServerConfig(
        id=sid,
        name=req.name,
        host=req.host,
        port=req.port,
        username=req.username,
        password=req.password or "",
        private_key=req.private_key or "",
        tags=req.tags,
    )
    settings.add_server(server)
    return server


@router.put("/{server_id}", response_model=ServerConfig)
async def update_server(server_id: str, req: ServerUpdateRequest):
    server = settings.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    update_data = req.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(server, field, value)

    settings.add_server(server)
    return server


@router.delete("/{server_id}")
async def delete_server(server_id: str):
    if not settings.remove_server(server_id):
        raise HTTPException(status_code=404, detail="Server not found")
    return {"message": "Server deleted successfully"}


@router.post("/{server_id}/test")
async def test_connection(server_id: str):
    server = settings.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    from ..core.ssh_pool import ssh_pool
    try:
        conn = ssh_pool.acquire(server)
        try:
            return {"success": True, "message": "Connection successful"}
        finally:
            ssh_pool.release(conn, keep=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Connection failed: {str(e)}")
