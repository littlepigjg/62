import glob
import re
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pathlib import Path

from ..config import LOGS_DIR

router = APIRouter(prefix="/logs", tags=["Logs"])


def _parse_log_file(file_path: Path) -> List[dict]:
    entries = []
    try:
        content = file_path.read_text(encoding="utf-8")
        pattern = r"={80}\nTask ID: (.*?)\nServer: (.*?) \((.*?)\)\nCommand: (.*?)\n(?:Script: (.*?)\n)?Start: (.*?)\nEnd: (.*?)\nStatus: (.*?), Exit Code: (.*?)\n-{40} OUTPUT -{40}\n(.*?)\n={80}"
        matches = re.findall(pattern, content, re.DOTALL)

        for m in matches:
            task_id, server_name, server_id, command, script_name, start_time, end_time, status, exit_code, output = m
            entries.append({
                "task_id": task_id,
                "server_name": server_name,
                "server_id": server_id,
                "command": command,
                "script_name": script_name if script_name else None,
                "start_time": start_time,
                "end_time": end_time,
                "status": status,
                "exit_code": int(exit_code) if exit_code != "None" else None,
                "output": output,
                "log_file": file_path.name,
            })
    except Exception:
        pass
    return entries


@router.get("")
async def list_logs(
    date: Optional[str] = Query(None, description="Format: YYYY-MM-DD"),
    server_id: Optional[str] = Query(None),
    limit: int = Query(100),
):
    log_files = sorted(LOGS_DIR.glob("*.log"), reverse=True)

    if date:
        log_files = [f for f in log_files if f.name.startswith(date)]

    all_entries = []
    for f in log_files:
        entries = _parse_log_file(f)
        if server_id:
            entries = [e for e in entries if e["server_id"] == server_id]
        for e in entries:
            e["output"] = e["output"][-5000:]
        all_entries.extend(entries)

    all_entries.sort(key=lambda x: x["start_time"], reverse=True)
    return all_entries[:limit]


@router.get("/dates")
async def list_log_dates():
    dates = set()
    for f in LOGS_DIR.glob("*.log"):
        match = re.match(r"(\d{4}-\d{2}-\d{2})", f.name)
        if match:
            dates.add(match.group(1))
    return sorted(dates, reverse=True)


@router.get("/{task_id}")
async def get_log_by_task(task_id: str):
    for f in sorted(LOGS_DIR.glob("*.log"), reverse=True):
        entries = _parse_log_file(f)
        for e in entries:
            if e["task_id"] == task_id:
                return e
    raise HTTPException(status_code=404, detail="Log entry not found")
