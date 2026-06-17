from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class CommandExecuteRequest(BaseModel):
    server_ids: List[str]
    command: str
    timeout: int = 300
    env: Dict[str, str] = Field(default_factory=dict)


class ScriptExecuteRequest(BaseModel):
    server_ids: List[str]
    script_content: str
    script_name: Optional[str] = "script.sh"
    interpreter: str = "bash"
    args: List[str] = Field(default_factory=list)
    timeout: int = 300


class TemplateCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    script_content: str
    interpreter: str = "bash"
    tags: List[str] = Field(default_factory=list)


class TemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    script_content: Optional[str] = None
    interpreter: Optional[str] = None
    tags: Optional[List[str]] = None


class ExecutionOutput(BaseModel):
    server_id: str
    server_name: str
    task_id: str
    stream: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ExecutionResult(BaseModel):
    task_id: str
    server_id: str
    server_name: str
    command: str
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    start_time: str
    end_time: Optional[str] = None
    status: str = "running"


class LogEntry(BaseModel):
    task_id: str
    server_id: str
    server_name: str
    command: str
    script_name: Optional[str] = None
    exit_code: Optional[int] = None
    start_time: str
    end_time: Optional[str] = None
    status: str
    output: str = ""
