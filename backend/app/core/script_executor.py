import base64
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from ..config import ServerConfig
from .ssh_pool import ssh_pool


CLEANUP_GLOB_PATTERNS = [
    "/tmp/script.sh_task-*",
    "/tmp/*_task-*",
    "$HOME/.cache/ssh_exec/*_task-*",
    "/var/tmp/*_task-*",
]

DEFAULT_CANDIDATE_DIRS = [
    "$HOME/.cache/ssh_exec",
    "$HOME/.ssh_exec_tmp",
    "/tmp",
    "/var/tmp",
    "/dev/shm",
    "$PWD/.ssh_exec_tmp",
]


@dataclass
class ExecutionPlan:
    mode: str
    command: str
    remote_path: Optional[str] = None
    remote_dir: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class CleanupRecord:
    server_id: str
    remote_path: str
    created_at: float
    attempts: int = 0
    max_attempts: int = 5


class TempPathSelector:
    def __init__(self, candidate_dirs: Optional[List[str]] = None):
        self.candidate_dirs = candidate_dirs or list(DEFAULT_CANDIDATE_DIRS)
        self._dir_cache: dict = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl: int = 3600

    def _expand(self, server: ServerConfig, path: str) -> str:
        expanded = path.replace("$HOME", f"/home/{server.username}")
        if server.username == "root":
            expanded = expanded.replace(f"/home/root", "/root")
        expanded = expanded.replace("$PWD", f"/home/{server.username}")
        if server.username == "root" and "/home/root" in expanded:
            expanded = expanded.replace(f"/home/{server.username}", "/root")
        return expanded

    def _check_dir(
        self,
        server: ServerConfig,
        dir_path: str,
        required_bytes: int,
    ) -> Tuple[bool, str]:
        check_script = f"""
        set -e;
        d="{dir_path}";
        mkdir -p "$d" 2>/dev/null || exit 1;
        [ -d "$d" ] || exit 2;
        [ -w "$d" ] || exit 3;
        avail=$(df -Pk "$d" 2>/dev/null | awk 'NR==2 {{print $4}}' || echo 0);
        [ "$avail" -ge "{(required_bytes // 1024) + 64}" ] 2>/dev/null || [ "$avail" = "" ] || exit 4;
        test_file="$d/.ssh_exec_wtest_$$_$RANDOM";
        (echo ok > "$test_file" 2>/dev/null && rm -f "$test_file") || exit 5;
        exec_test="$d/.ssh_exec_etest_$$_$RANDOM";
        (echo '#!/bin/sh' > "$exec_test" && chmod +x "$exec_test" 2>/dev/null && "$exec_test" 2>/dev/null; rc=$?; rm -f "$exec_test"; [ "$rc" -le 126 ]) || exit 6;
        exit 0;
        """
        try:
            exit_code, _, stderr = ssh_pool.execute_command(
                server, check_script, timeout=15,
            )
            if exit_code == 0:
                return True, "ok"
            reason_map = {
                1: "mkdir failed",
                2: "not a dir",
                3: "not writable",
                4: "insufficient space",
                5: "write test failed",
                6: "noexec mount or cannot execute",
            }
            return False, reason_map.get(exit_code, f"unknown ({exit_code})")
        except Exception as e:
            return False, f"exception: {type(e).__name__}"

    def select(
        self,
        server: ServerConfig,
        script_size: int,
    ) -> Tuple[Optional[str], List[str]]:
        cache_key = server.id
        now = time.time()
        notes: List[str] = []

        with self._cache_lock:
            cached = self._dir_cache.get(cache_key)
            if cached and (now - cached["ts"]) < self._cache_ttl:
                return cached["dir"], cached.get("notes", [])

        chosen: Optional[str] = None
        for raw_dir in self.candidate_dirs:
            dir_path = self._expand(server, raw_dir)
            ok, reason = self._check_dir(server, dir_path, script_size)
            if ok:
                chosen = dir_path
                notes.append(f"dir chosen: {dir_path} (from candidate '{raw_dir}')")
                break
            else:
                notes.append(f"skip {dir_path}: {reason}")

        with self._cache_lock:
            self._dir_cache[cache_key] = {
                "ts": now,
                "dir": chosen,
                "notes": notes[-3:],
            }
        return chosen, notes


class CleanupManager:
    def __init__(self):
        self._pending: List[CleanupRecord] = []
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            try:
                time.sleep(15)
                self._process_once()
            except Exception:
                time.sleep(30)

    def _process_once(self) -> None:
        with self._lock:
            pending = list(self._pending)
            self._pending = []

        still_pending: List[CleanupRecord] = []
        for rec in pending:
            from .ssh_pool import ssh_pool
            from ..config import settings

            server = settings.get_server(rec.server_id)
            if not server:
                continue
            ok = self._try_remove(server, rec.remote_path)
            if not ok:
                rec.attempts += 1
                if rec.attempts < rec.max_attempts:
                    still_pending.append(rec)

        with self._lock:
            self._pending.extend(still_pending)

    def _try_remove(self, server: ServerConfig, path: str) -> bool:
        dir_name = os.path.dirname(path) or "/"
        base = os.path.basename(path)
        multi_cmd = f"""
        set +e;
        [ -n "{path}" ] || exit 0;
        ls -la "{path}" >/dev/null 2>&1 || exit 0;
        (chattr -i "{path}" 2>/dev/null; true);
        (chmod 777 "{path}" 2>/dev/null; true);
        rm -f "{path}" 2>/dev/null;
        ls -la "{path}" >/dev/null 2>&1 || exit 0;
        (find "{dir_name}" -maxdepth 1 -name "{base}" -type f -delete 2>/dev/null; true);
        ls -la "{path}" >/dev/null 2>&1 && exit 1 || exit 0;
        """
        try:
            exit_code, _, _ = ssh_pool.execute_command(server, multi_cmd, timeout=10)
            return exit_code == 0
        except Exception:
            return False

    def schedule_cleanup(self, server_id: str, remote_path: Optional[str]) -> None:
        if not remote_path:
            return
        self._pending.append(CleanupRecord(
            server_id=server_id,
            remote_path=remote_path,
            created_at=time.time(),
            max_attempts=5,
        ))

    def force_cleanup(self, server: ServerConfig, remote_path: str) -> None:
        self._try_remove(server, remote_path)
        self.schedule_cleanup(server.id, remote_path)

    def bulk_cleanup_leftovers(self, server: ServerConfig) -> int:
        base_patterns = " ".join([
            "/tmp/script.sh_task-* /tmp/*_task-* /var/tmp/*_task-* "
            "/dev/shm/*_task-* $HOME/.cache/ssh_exec/*_task-* $HOME/.ssh_exec_tmp/*_task-*"
        ])
        cmd = f"""
        set +e;
        count=0;
        for p in {base_patterns}; do
          for f in $p; do
            [ -e "$f" ] || continue;
            (chattr -i "$f" 2>/dev/null; true);
            (chmod 777 "$f" 2>/dev/null; true);
            rm -f "$f" 2>/dev/null || true;
            [ -e "$f" ] || count=$((count+1));
          done;
        done;
        echo "$count";
        """
        try:
            _, stdout, _ = ssh_pool.execute_command(server, cmd, timeout=20)
            for line in stdout.strip().splitlines():
                if line.strip().isdigit():
                    return int(line.strip())
        except Exception:
            pass
        return 0


class ScriptExecutor:
    def __init__(self):
        self.path_selector = TempPathSelector()
        self.cleanup = CleanupManager()
        self.cleanup.start()
        self._pipe_size_threshold: int = 64 * 1024

    def _encode_script(self, content: str) -> str:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        return encoded

    def _build_pipe_command(
        self,
        interpreter: str,
        b64_content: str,
        args: List[str],
        env: Optional[dict] = None,
    ) -> str:
        args_str = " ".join(f"'{a.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for a in args)
        env_prefix = ""
        if env:
            env_parts = [f"{k}='{str(v).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for k, v in env.items()]
            env_prefix = "export " + " ".join(env_parts) + " 2>/dev/null; "

        decode_cmd = (
            f"python3 -c 'import sys,base64; sys.stdout.buffer.write(base64.b64decode(sys.argv[1]))' '{b64_content}' 2>/dev/null "
            f"|| base64 -d <<'__B64_EOF__'\n{b64_content}\n__B64_EOF__"
        )
        return (
            f"set +e; {env_prefix}"
            f"_tmpout=$({decode_cmd} 2>/dev/null | {interpreter} -s -- {args_str}; echo _PIPERC_$?)"
            f"; _rc=$?; echo \"${{_tmpout}}\"; exit 0"
        )

    def _build_heredoc_command(
        self,
        interpreter: str,
        script_content: str,
        args: List[str],
        env: Optional[dict] = None,
    ) -> str:
        marker = "__SCRIPT_EOF_" + str(int(time.time() * 1000)) + "__"
        safe_content = script_content.replace(marker, "_EOF_REPLACED_")

        args_str = " ".join(f"'{a.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for a in args)
        env_prefix = ""
        if env:
            env_parts = [f"{k}='{str(v).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for k, v in env.items()]
            env_prefix = "export " + " ".join(env_parts) + " 2>/dev/null; "

        return (
            f"set +e; {env_prefix}"
            f"{interpreter} /dev/stdin {args_str} <<'{marker}'\n"
            f"{safe_content}\n"
            f"{marker}\n"
            f"echo _HERERC_$?"
        )

    def plan_execution(
        self,
        server: ServerConfig,
        script_content: str,
        script_name: str,
        interpreter: str,
        args: List[str],
        task_id: str,
    ) -> ExecutionPlan:
        content_size = len(script_content.encode("utf-8"))
        notes: List[str] = []

        if content_size <= self._pipe_size_threshold:
            try:
                b64 = self._encode_script(script_content)
                if len(b64) < 120000:
                    cmd = self._build_pipe_command(interpreter, b64, args)
                    notes.append(f"using pipe mode (size={content_size}, b64={len(b64)})")
                    return ExecutionPlan(mode="pipe", command=cmd, notes=notes)
            except Exception as e:
                notes.append(f"pipe encode skipped: {e}")

        if content_size < 8 * 1024 and "\x00" not in script_content:
            has_unsafe = bool(re.search(r"^" + re.escape("__SCRIPT_EOF_"), script_content, re.M))
            if not has_unsafe:
                cmd = self._build_heredoc_command(interpreter, script_content, args)
                notes.append(f"using heredoc mode (size={content_size})")
                return ExecutionPlan(mode="heredoc", command=cmd, notes=notes)

        chosen_dir, dir_notes = self.path_selector.select(server, content_size + 4096)
        notes.extend(dir_notes)
        if chosen_dir:
            safe_base = re.sub(r"[^A-Za-z0-9._-]", "_", script_name)
            remote_path = f"{chosen_dir}/{safe_base}_{task_id}"
            cmd = f"(chmod +x {remote_path} 2>/dev/null; true); {interpreter} {remote_path} " + " ".join(args)
            notes.append(f"using file mode, upload to {remote_path}")
            return ExecutionPlan(mode="file", command=cmd, remote_path=remote_path, remote_dir=chosen_dir, notes=notes)

        fallback_cmd = self._build_heredoc_command(interpreter, script_content, args)
        notes.append("fallback: all dirs failed, force heredoc")
        return ExecutionPlan(mode="heredoc_fallback", command=fallback_cmd, notes=notes)

    def execute(
        self,
        server: ServerConfig,
        plan: ExecutionPlan,
        script_content: Optional[str],
        timeout: int,
        stream_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[int, str, str]:
        if plan.mode == "file" and plan.remote_path:
            self._ensure_dir(server, plan.remote_dir)
            upload_ok = self._safe_upload(server, script_content or "", plan.remote_path)
            if not upload_ok:
                msg = f"[WARN] Failed to upload script to {plan.remote_path}, switching to heredoc.\n"
                if stream_callback:
                    stream_callback("stderr", msg)
                fallback = self._build_heredoc_command(
                    "bash" if not plan.command.split() else plan.command.split()[0],
                    script_content or "",
                    [],
                )
                plan = ExecutionPlan(mode="heredoc_fallback", command=fallback, notes=["file upload failed"])
            else:
                try:
                    self._probe_exec_perm(server, plan.remote_path)
                except Exception as e:
                    if stream_callback:
                        stream_callback("stderr", f"[WARN] exec probe issue: {e}\n")

        try:
            exit_code, stdout, stderr = ssh_pool.execute_command(
                server=server,
                command=plan.command,
                timeout=timeout,
                stream_callback=stream_callback,
            )
        except Exception as e:
            raise

        if plan.mode == "file" and plan.remote_path:
            self.cleanup.schedule_cleanup(server.id, plan.remote_path)
            threading.Thread(
                target=self.cleanup.force_cleanup,
                args=(server, plan.remote_path),
                daemon=True,
            ).start()

        if exit_code is None:
            exit_code = -1

        match = re.search(r"_PIPERC_(-?\d+)\s*$", stdout)
        if not match:
            match = re.search(r"_HERERC_(-?\d+)\s*$", stdout)
        if match and plan.mode in ("pipe", "heredoc", "heredoc_fallback"):
            try:
                real_code = int(match.group(1))
                stdout = stdout[: match.start()]
                if not stdout.endswith("\n") and stdout:
                    stdout += "\n"
                exit_code = real_code
            except Exception:
                pass

        return exit_code, stdout, stderr

    def _ensure_dir(self, server: ServerConfig, remote_dir: Optional[str]) -> None:
        if not remote_dir:
            return
        try:
            ssh_pool.execute_command(server, f"mkdir -p {remote_dir} 2>/dev/null; true", timeout=5)
        except Exception:
            pass

    def _safe_upload(self, server: ServerConfig, content: str, remote_path: str) -> bool:
        modes_to_try = ["0700", "0755", "0644", "0600"]
        last_error: Optional[str] = None

        for mode in modes_to_try:
            try:
                ssh_pool.upload_content(server, content, remote_path, mode)
                return True
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                try:
                    ssh_pool.execute_command(server, f"rm -f {remote_path} 2>/dev/null; true", timeout=5)
                except Exception:
                    pass

        try:
            encoded = self._encode_script(content)
            dirp = os.path.dirname(remote_path) or "/tmp"
            base = os.path.basename(remote_path)
            cmd = (
                f"mkdir -p {dirp} 2>/dev/null; "
                f"python3 -c \"import base64,sys;open(sys.argv[1],'wb').write(base64.b64decode(sys.argv[2]))\" '{remote_path}' '{encoded}' 2>/dev/null "
                f"|| (echo '{encoded}' | base64 -d > '{remote_path}' 2>/dev/null; true); "
                f"[ -s '{remote_path}' ] && chmod 700 '{remote_path}' 2>/dev/null; "
                f"[ -s '{remote_path}' ] && exit 0 || exit 1"
            )
            exit_code, _, _ = ssh_pool.execute_command(server, cmd, timeout=15)
            return exit_code == 0
        except Exception as e:
            last_error = f"fallback upload also failed: {e}"

        return False

    def _probe_exec_perm(self, server: ServerConfig, remote_path: str) -> None:
        try:
            ssh_pool.execute_command(
                server,
                f"(chmod +x {remote_path} 2>/dev/null; [ -x {remote_path} ] || chmod 755 {remote_path} 2>/dev/null; true)",
                timeout=5,
            )
        except Exception:
            pass


script_executor = ScriptExecutor()
