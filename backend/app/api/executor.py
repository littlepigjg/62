import asyncio
from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from datetime import datetime

from ..config import settings
from ..models import (
    CommandExecuteRequest,
    ScriptExecuteRequest,
    ExecutionResult,
)
from ..core.scheduler import scheduler
from ..core.stream import stream_manager

router = APIRouter(prefix="/execute", tags=["Execute"])


def _register_stream_callbacks(task_ids: List[str]) -> None:
    for tid in task_ids:
        task = scheduler.get_task(tid)
        if not task:
            continue

        def _make_output_cb(tid_copy, sid_copy, sname_copy):
            async def _cb(_tid, _sid, _sname, stream, content):
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    return
                stream_manager.publish_output(tid_copy, sid_copy, sname_copy, stream, content)
            return _cb

        def _make_done_cb(tid_copy, sid_copy, sname_copy):
            def _cb(task_obj):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            _async_done_cb(task_obj),
                            loop,
                        )
                except RuntimeError:
                    pass

            async def _async_done_cb(task_obj):
                exit_code = task_obj.exit_code
                status = task_obj.status
                stream_manager.publish_status(tid_copy, sid_copy, sname_copy, status, exit_code)

            return _cb

        task.output_callbacks.append(_make_output_cb(tid, task.server_id, task.server_name))
        task.done_callbacks.append(_make_done_cb(tid, task.server_id, task.server_name))


@router.post("/command", response_model=List[ExecutionResult])
async def execute_command(req: CommandExecuteRequest, background_tasks: BackgroundTasks):
    valid_server_ids = []
    for sid in req.server_ids:
        if settings.get_server(sid):
            valid_server_ids.append(sid)
        else:
            raise HTTPException(status_code=404, detail=f"Server '{sid}' not found")

    if not valid_server_ids:
        raise HTTPException(status_code=400, detail="No valid servers specified")

    results = scheduler.execute_command(
        server_ids=valid_server_ids,
        command=req.command,
        timeout=req.timeout,
        env=req.env,
    )

    task_ids = [r.task_id for r in results]
    _register_stream_callbacks(task_ids)

    return results


@router.post("/script", response_model=List[ExecutionResult])
async def execute_script(req: ScriptExecuteRequest, background_tasks: BackgroundTasks):
    valid_server_ids = []
    for sid in req.server_ids:
        if settings.get_server(sid):
            valid_server_ids.append(sid)
        else:
            raise HTTPException(status_code=404, detail=f"Server '{sid}' not found")

    if not valid_server_ids:
        raise HTTPException(status_code=400, detail="No valid servers specified")

    results = scheduler.execute_script(
        server_ids=valid_server_ids,
        script_content=req.script_content,
        script_name=req.script_name or "script.sh",
        interpreter=req.interpreter,
        args=req.args,
        timeout=req.timeout,
    )

    task_ids = [r.task_id for r in results]
    _register_stream_callbacks(task_ids)

    return results


@router.get("/tasks", response_model=List[ExecutionResult])
async def list_tasks(server_id: Optional[str] = None, limit: int = 100):
    tasks = scheduler.list_tasks(server_id=server_id, limit=limit)
    results = []
    for t in tasks:
        results.append(ExecutionResult(
            task_id=t.task_id,
            server_id=t.server_id,
            server_name=t.server_name,
            command=t.command,
            exit_code=t.exit_code,
            stdout=t.stdout[-10000:] if len(t.stdout) > 10000 else t.stdout,
            stderr=t.stderr[-5000:] if len(t.stderr) > 5000 else t.stderr,
            start_time=t.start_time.isoformat(),
            end_time=t.end_time.isoformat() if t.end_time else None,
            status=t.status,
        ))
    return results


@router.get("/tasks/{task_id}", response_model=ExecutionResult)
async def get_task(task_id: str):
    task = scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return ExecutionResult(
        task_id=task.task_id,
        server_id=task.server_id,
        server_name=task.server_name,
        command=task.command,
        exit_code=task.exit_code,
        stdout=task.stdout,
        stderr=task.stderr,
        start_time=task.start_time.isoformat(),
        end_time=task.end_time.isoformat() if task.end_time else None,
        status=task.status,
    )
