import asyncio
import os
import threading
import uuid
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any, Set
from dataclasses import dataclass, field

from ..config import settings, ServerConfig, LOGS_DIR
from ..models import ExecutionResult, LogEntry
from .ssh_pool import ssh_pool
from .script_executor import script_executor


@dataclass
class Task:
    task_id: str
    server_id: str
    server_name: str
    task_type: str
    command: str
    script_name: Optional[str]
    start_time: datetime
    end_time: Optional[datetime] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    status: str = "pending"
    output_callbacks: List[Callable] = field(default_factory=list)
    done_callbacks: List[Callable] = field(default_factory=list)


class CommandScheduler:
    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._tasks_lock = threading.Lock()
        self._semaphore = threading.Semaphore(settings.max_concurrent_tasks)
        self._active_server_tasks: Dict[str, Set[str]] = {}

    def _generate_task_id(self) -> str:
        return f"task-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._tasks_lock:
            return self._tasks.get(task_id)

    def list_tasks(self, server_id: Optional[str] = None, limit: int = 100) -> List[Task]:
        with self._tasks_lock:
            tasks = list(self._tasks.values())
        if server_id:
            tasks = [t for t in tasks if t.server_id == server_id]
        tasks.sort(key=lambda t: t.start_time, reverse=True)
        return tasks[:limit]

    def register_output_callback(self, task_id: str, callback: Callable) -> None:
        task = self.get_task(task_id)
        if task:
            task.output_callbacks.append(callback)

    def register_done_callback(self, task_id: str, callback: Callable) -> None:
        task = self.get_task(task_id)
        if task:
            task.done_callbacks.append(callback)

    def _trigger_output_callbacks(self, task: Task, stream: str, content: str) -> None:
        for cb in task.output_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(cb(task.task_id, task.server_id, task.server_name, stream, content))
                    loop.close()
                else:
                    cb(task.task_id, task.server_id, task.server_name, stream, content)
            except Exception:
                pass

    def _trigger_done_callbacks(self, task: Task) -> None:
        for cb in task.done_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(cb(task))
                    loop.close()
                else:
                    cb(task)
            except Exception:
                pass

    def _run_command_task(self, task: Task, server: ServerConfig, timeout: int, env: Dict[str, str]) -> None:
        task.status = "running"
        try:
            def stream_cb(stream: str, content: str) -> None:
                if stream == "stdout":
                    task.stdout += content
                else:
                    task.stderr += content
                self._trigger_output_callbacks(task, stream, content)

            exit_code, stdout, stderr = ssh_pool.execute_command(
                server=server,
                command=task.command,
                timeout=timeout,
                env=env,
                stream_callback=stream_cb,
            )
            task.exit_code = exit_code
            task.status = "success" if exit_code == 0 else "failed"
        except Exception as e:
            task.stderr += f"\n[ERROR] {type(e).__name__}: {str(e)}\n"
            self._trigger_output_callbacks(task, "stderr", f"\n[ERROR] {type(e).__name__}: {str(e)}\n")
            task.exit_code = -1
            task.status = "error"
        finally:
            task.end_time = datetime.now()
            self._cleanup_server_task(task.server_id, task.task_id)
            self._log_task(task)
            self._trigger_done_callbacks(task)
            self._semaphore.release()

    def _run_script_task(
        self,
        task: Task,
        server: ServerConfig,
        script_content: str,
        script_name: str,
        interpreter: str,
        args: List[str],
        timeout: int,
    ) -> None:
        task.status = "running"
        plan = None
        try:
            plan = script_executor.plan_execution(
                server=server,
                script_content=script_content,
                script_name=script_name,
                interpreter=interpreter,
                args=args,
                task_id=task.task_id,
            )

            task.command = plan.command if len(plan.command) < 400 else plan.command[:400] + "..."
            mode_hint = f"[exec-mode={plan.mode}] " + " ".join(plan.notes[-2:]) + "\n"

            def stream_cb(stream: str, content: str) -> None:
                if stream == "stdout":
                    task.stdout += content
                else:
                    task.stderr += content
                self._trigger_output_callbacks(task, stream, content)

            stream_cb("stderr", mode_hint)

            exit_code, stdout, stderr = script_executor.execute(
                server=server,
                plan=plan,
                script_content=script_content,
                timeout=timeout,
                stream_callback=stream_cb,
            )

            if plan.remote_path and plan.mode == "file":
                script_executor.cleanup.schedule_cleanup(server.id, plan.remote_path)
                try:
                    script_executor.cleanup.force_cleanup(server, plan.remote_path)
                except Exception:
                    pass

            task.exit_code = exit_code
            task.status = "success" if (exit_code is not None and exit_code == 0) else "failed"

            if stdout and not task.stdout.endswith(stdout):
                task.stdout += stdout
            if stderr and not task.stderr.endswith(stderr):
                task.stderr += stderr

        except Exception as e:
            task.stderr += f"\n[ERROR] {type(e).__name__}: {str(e)}\n"
            self._trigger_output_callbacks(task, "stderr", f"\n[ERROR] {type(e).__name__}: {str(e)}\n")
            task.exit_code = -1
            task.status = "error"

            if plan and plan.remote_path and plan.mode == "file":
                try:
                    script_executor.cleanup.schedule_cleanup(server.id, plan.remote_path)
                    script_executor.cleanup.force_cleanup(server, plan.remote_path)
                except Exception:
                    pass
        finally:
            task.end_time = datetime.now()
            self._cleanup_server_task(task.server_id, task.task_id)
            self._log_task(task)
            self._trigger_done_callbacks(task)
            self._semaphore.release()

    def _cleanup_server_task(self, server_id: str, task_id: str) -> None:
        if server_id in self._active_server_tasks:
            self._active_server_tasks[server_id].discard(task_id)

    def _log_task(self, task: Task) -> None:
        try:
            date_str = task.start_time.strftime("%Y-%m-%d")
            log_file = LOGS_DIR / f"{date_str}_{task.server_id}.log"

            output = task.stdout + task.stderr
            log_entry = LogEntry(
                task_id=task.task_id,
                server_id=task.server_id,
                server_name=task.server_name,
                command=task.command,
                script_name=task.script_name,
                exit_code=task.exit_code,
                start_time=task.start_time.isoformat(),
                end_time=task.end_time.isoformat() if task.end_time else None,
                status=task.status,
                output=output,
            )

            with open(log_file, "a", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write(f"Task ID: {log_entry.task_id}\n")
                f.write(f"Server: {log_entry.server_name} ({log_entry.server_id})\n")
                f.write(f"Command: {log_entry.command}\n")
                if log_entry.script_name:
                    f.write(f"Script: {log_entry.script_name}\n")
                f.write(f"Start: {log_entry.start_time}\n")
                f.write(f"End: {log_entry.end_time}\n")
                f.write(f"Status: {log_entry.status}, Exit Code: {log_entry.exit_code}\n")
                f.write("-" * 40 + " OUTPUT " + "-" * 40 + "\n")
                f.write(log_entry.output)
                f.write("\n" + "=" * 80 + "\n\n")
        except Exception:
            pass

    def execute_command(
        self,
        server_ids: List[str],
        command: str,
        timeout: int = 300,
        env: Optional[Dict[str, str]] = None,
    ) -> List[ExecutionResult]:
        env = env or {}
        results: List[ExecutionResult] = []
        threads: List[threading.Thread] = []

        for sid in server_ids:
            server = settings.get_server(sid)
            if not server:
                continue

            task_id = self._generate_task_id()
            task = Task(
                task_id=task_id,
                server_id=sid,
                server_name=server.name,
                task_type="command",
                command=command,
                script_name=None,
                start_time=datetime.now(),
            )

            with self._tasks_lock:
                self._tasks[task_id] = task
                if sid not in self._active_server_tasks:
                    self._active_server_tasks[sid] = set()
                self._active_server_tasks[sid].add(task_id)

            results.append(ExecutionResult(
                task_id=task_id,
                server_id=sid,
                server_name=server.name,
                command=command,
                start_time=task.start_time.isoformat(),
                status="pending",
            ))

            self._semaphore.acquire()
            t = threading.Thread(
                target=self._run_command_task,
                args=(task, server, timeout, env),
                daemon=True,
            )
            threads.append(t)
            t.start()

        return results

    def execute_script(
        self,
        server_ids: List[str],
        script_content: str,
        script_name: str = "script.sh",
        interpreter: str = "bash",
        args: Optional[List[str]] = None,
        timeout: int = 300,
    ) -> List[ExecutionResult]:
        args = args or []
        results: List[ExecutionResult] = []
        threads: List[threading.Thread] = []

        for sid in server_ids:
            server = settings.get_server(sid)
            if not server:
                continue

            task_id = self._generate_task_id()
            task = Task(
                task_id=task_id,
                server_id=sid,
                server_name=server.name,
                task_type="script",
                command="",
                script_name=script_name,
                start_time=datetime.now(),
            )

            with self._tasks_lock:
                self._tasks[task_id] = task
                if sid not in self._active_server_tasks:
                    self._active_server_tasks[sid] = set()
                self._active_server_tasks[sid].add(task_id)

            results.append(ExecutionResult(
                task_id=task_id,
                server_id=sid,
                server_name=server.name,
                command=f"{interpreter} {script_name}",
                start_time=task.start_time.isoformat(),
                status="pending",
            ))

            self._semaphore.acquire()
            t = threading.Thread(
                target=self._run_script_task,
                args=(task, server, script_content, script_name, interpreter, args, timeout),
                daemon=True,
            )
            threads.append(t)
            t.start()

        return results


scheduler = CommandScheduler()
