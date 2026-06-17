import base64
import os
import re
import secrets
import threading
import time
import hashlib
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from ..config import ServerConfig
from .ssh_pool import ssh_pool


CLEANUP_GLOB_PATTERNS = [
    "/tmp/script.sh_task-*",
    "/tmp/*_task-*",
    "$HOME/.cache/ssh_exec/*_task-*",
    "/var/tmp/*_task-*",
    "/dev/shm/*_task-*",
    "$HOME/.ssh_exec_tmp/*_task-*",
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
    extra_cleanups: List[str] = field(default_factory=list)


@dataclass
class CleanupRecord:
    server_id: str
    remote_path: str
    created_at: float
    attempts: int = 0
    max_attempts: int = 12


class TempPathSelector:
    def __init__(self, candidate_dirs: Optional[List[str]] = None):
        self.candidate_dirs = candidate_dirs or list(DEFAULT_CANDIDATE_DIRS)
        self._dir_cache: dict = {}
        self._checking: dict = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl: int = 90
        self._soft_check_enabled: bool = True

    def invalidate_server(self, server_id: str) -> None:
        with self._cache_lock:
            self._dir_cache.pop(server_id, None)
            self._checking.pop(server_id, None)

    def _expand(self, server: ServerConfig, path: str) -> str:
        if server.username == "root":
            home = "/root"
        else:
            home = f"/home/{server.username}"
        expanded = path.replace("$HOME", home)
        expanded = expanded.replace("$PWD", home)
        return expanded

    def _check_dir(
        self,
        server: ServerConfig,
        dir_path: str,
        required_bytes: int,
    ) -> Tuple[bool, str]:
        required_kb = max(64, (required_bytes // 1024) + 256)
        check_script = rf"""
        set +e
        d={dir_path!r}
        mkdir -p "$d" 2>/dev/null || {{ echo "STAGE1_FAIL"; exit 1; }}
        [ -d "$d" ] || {{ echo "STAGE2_FAIL"; exit 2; }}
        [ -w "$d" ] || {{ echo "STAGE3_FAIL"; exit 3; }}
        avail=$(df -Pk "$d" 2>/dev/null | awk 'NR==2 {{print $4}}' || echo 0)
        avail_num=${{avail:-0}}
        case "$avail_num" in
            ''|*[!0-9]*) avail_num=0 ;;
        esac
        if [ "$avail_num" -gt 0 ] 2>/dev/null && [ "$avail_num" -lt {required_kb} ] 2>/dev/null; then
            echo "STAGE4_FAIL avail=$avail_num"
            exit 4
        fi
        rand_suffix=$(date +%s%N 2>/dev/null || echo $RANDOM$RANDOM)
        test_file="$d/.ssh_exec_wtest_$$_$rand_suffix"
        if ! ( echo ok > "$test_file" 2>/dev/null && rm -f "$test_file" ); then
            echo "STAGE5_FAIL"
            exit 5
        fi
        exec_test="$d/.ssh_exec_etest_$$_$rand_suffix"
        (
            printf '#!/bin/sh\\nexit 0\\n' > "$exec_test" 2>/dev/null
            chmod +x "$exec_test" 2>/dev/null
            "$exec_test" 2>/dev/null
            rc=$?
            rm -f "$exec_test" 2>/dev/null
            if [ "$rc" -gt 126 ] 2>/dev/null; then exit 6; fi
        ) || {{ echo "STAGE6_FAIL"; exit 6; }}
        echo "STAGE_OK dir=$d avail=${{avail_num:-unknown}}"
        exit 0
        """
        try:
            exit_code, stdout, stderr = ssh_pool.execute_command(
                server, check_script, timeout=20,
            )
            out = (stdout + stderr).strip()
            if exit_code == 0 and "STAGE_OK" in out:
                return True, out.split("STAGE_OK")[-1].strip() or "ok"
            reason_map = {
                1: "mkdir failed",
                2: "not a dir",
                3: "not writable",
                4: "insufficient space",
                5: "write+unlink test failed",
                6: "noexec mount / cannot execute",
            }
            detail = ""
            for line in out.splitlines():
                if "STAGE" in line and "_FAIL" in line:
                    detail = " " + line.strip()
                    break
            return False, reason_map.get(exit_code, f"unknown ({exit_code})") + detail
        except Exception as e:
            return False, f"exception: {type(e).__name__}: {e}"

    def _soft_verify_dir(
        self,
        server: ServerConfig,
        dir_path: str,
        required_bytes: int,
        cached_avail_kb: int,
    ) -> Tuple[bool, str]:
        required_kb = max(64, (required_bytes // 1024) + 256)
        if cached_avail_kb and cached_avail_kb < required_kb:
            return False, f"cached avail {cached_avail_kb}KB < {required_kb}KB"
        verify_script = rf"""
        set +e
        d={dir_path!r}
        [ -d "$d" ] || {{ echo "FAIL: not a dir"; exit 2; }}
        [ -w "$d" ] || {{ echo "FAIL: not writable"; exit 3; }}
        avail=$(df -Pk "$d" 2>/dev/null | awk 'NR==2 {{print $4}}' || echo 0)
        avail_num=${{avail:-0}}
        case "$avail_num" in
            ''|*[!0-9]*) avail_num=0 ;;
        esac
        if [ "$avail_num" -gt 0 ] 2>/dev/null && [ "$avail_num" -lt {required_kb} ] 2>/dev/null; then
            echo "FAIL: avail=$avail_num KB < {required_kb}"
            exit 4
        fi
        rnd=$(date +%s%N 2>/dev/null || echo $RANDOM)
        sf="$d/.ssh_exec_sv_$$_$rnd"
        ( echo "sv" > "$sf" 2>/dev/null && rm -f "$sf" ) || {{ echo "FAIL: write test"; exit 5; }}
        echo "OK avail=${{avail_num:-unknown}}"
        exit 0
        """
        try:
            exit_code, stdout, stderr = ssh_pool.execute_command(
                server, verify_script, timeout=10,
            )
            out = (stdout + stderr).strip()
            if exit_code == 0 and "OK" in out:
                m = re.search(r"avail=(\d+)", out)
                avail = int(m.group(1)) if m else 0
                return True, f"soft-ok avail={avail}KB"
            return False, out.splitlines()[-1] if out else f"exit {exit_code}"
        except Exception as e:
            return False, f"exception: {type(e).__name__[:20]}"

    def select(
        self,
        server: ServerConfig,
        script_size: int,
        force_refresh: bool = False,
        soft_check: Optional[bool] = None,
    ) -> Tuple[Optional[str], List[str]]:
        cache_key = server.id
        now = time.time()
        notes: List[str] = []
        do_soft = self._soft_check_enabled if soft_check is None else soft_check

        if not force_refresh:
            cached_hit = False
            cached_dir_val = None
            cached_size_val = 0
            cached_avail_val = 0
            cached_notes_val: list = []
            cached_age = 0
            size_mismatch_skip = False
            with self._cache_lock:
                cached = self._dir_cache.get(cache_key)
                if cached and (now - cached["ts"]) < self._cache_ttl:
                    cached_size_val = cached.get("size", 0)
                    cached_avail_val = cached.get("avail_kb", 0)
                    cached_dir_val = cached["dir"]
                    cached_notes_val = list(cached.get("notes", []))
                    cached_age = int(now - cached["ts"])
                    if script_size > cached_size_val * 1.5 and cached_size_val > 0:
                        notes.append(
                            f"cached dir skipped (size mismatch: need={script_size} > 1.5x cached={cached_size_val})"
                        )
                        size_mismatch_skip = True
                    else:
                        cached_hit = True
                        self._dir_cache.pop(cache_key, None)

            if cached_hit:
                if do_soft:
                    ok, sreason = self._soft_verify_dir(
                        server, cached_dir_val, script_size, cached_avail_val,
                    )
                    if ok:
                        notes.append(f"cached dir + soft-verified (age={cached_age}s, {sreason})")
                        with self._cache_lock:
                            self._dir_cache[cache_key] = {
                                "ts": now,
                                "dir": cached_dir_val,
                                "size": max(script_size, cached_size_val),
                                "avail_kb": cached_avail_val,
                                "notes": cached_notes_val + notes,
                            }
                        return cached_dir_val, cached_notes_val + notes
                    else:
                        notes.append(f"soft-check FAILED for cached dir: {sreason}, rechecking...")
                        self.invalidate_server(server.id)
                else:
                    notes.append(f"using cached dir (age={cached_age}s, no soft-check)")
                    return cached_dir_val, cached_notes_val + notes
        elif force_refresh:
            self.invalidate_server(cache_key)

        with self._cache_lock:
            other_ts = self._checking.get(cache_key)
            if other_ts and (now - other_ts) < 15:
                pass
            else:
                self._checking[cache_key] = now

        chosen: Optional[str] = None
        chosen_avail_kb: int = 0
        for raw_dir in self.candidate_dirs:
            dir_path = self._expand(server, raw_dir)
            ok, reason = self._check_dir(server, dir_path, script_size)
            if ok:
                chosen = dir_path
                m = re.search(r"avail=(\d+)", reason)
                chosen_avail_kb = int(m.group(1)) if m else 0
                notes.append(f"dir chosen: {dir_path} (cand='{raw_dir}', {reason})")
                break
            else:
                notes.append(f"skip {dir_path}: {reason}")

        with self._cache_lock:
            self._checking.pop(cache_key, None)
            if chosen:
                self._dir_cache[cache_key] = {
                    "ts": now,
                    "dir": chosen,
                    "size": script_size,
                    "avail_kb": chosen_avail_kb,
                    "notes": notes[-3:],
                }
            else:
                self._dir_cache.pop(cache_key, None)

        return chosen, notes


class CleanupManager:
    def __init__(self):
        self._pending: List[CleanupRecord] = []
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._bulk_worker: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run_cleanup, daemon=True, name="ssh-exec-cleanup")
        self._worker.start()
        self._bulk_worker = threading.Thread(target=self._run_bulk, daemon=True, name="ssh-exec-bulk-clean")
        self._bulk_worker.start()

    def stop(self) -> None:
        self._running = False

    def _run_cleanup(self) -> None:
        while self._running:
            try:
                time.sleep(10)
                self._process_once()
            except Exception:
                time.sleep(20)

    def _run_bulk(self) -> None:
        last_run_per_server: dict = {}
        while self._running:
            try:
                time.sleep(180)
                from ..config import settings
                now = time.time()
                for srv in settings.servers:
                    last = last_run_per_server.get(srv.id, 0)
                    if now - last >= 1800:
                        try:
                            self.bulk_cleanup_leftovers(srv)
                            last_run_per_server[srv.id] = now
                        except Exception:
                            pass
            except Exception:
                time.sleep(300)

    def _process_once(self) -> None:
        with self._lock:
            pending = list(self._pending)
            self._pending = []

        still_pending: List[CleanupRecord] = []
        for rec in pending:
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
        if not path:
            return True
        dir_name = os.path.dirname(path) or "/"
        base = os.path.basename(path)
        base_escaped = base.replace("'", "'\\''")
        dir_escaped = dir_name.replace("'", "'\\''")
        path_escaped = path.replace("'", "'\\''")
        multi_cmd = rf"""
        set +e
        p={path_escaped!r}
        d={dir_escaped!r}
        b={base_escaped!r}
        [ -z "$p" ] && exit 0
        ls -la "$p" >/dev/null 2>&1 || exit 0
        (chattr -i "$p" 2>/dev/null; true)
        (lsattr -R "$d" 2>/dev/null | grep '---i-' | awk '{{print $2}}' | while read fp; do chattr -i "$fp" 2>/dev/null; done; true)
        (chmod 777 "$p" 2>/dev/null; true)
        (chown $(id -u):$(id -g) "$p" 2>/dev/null; true)
        rm -f "$p" 2>/dev/null
        unlink "$p" 2>/dev/null
        ls -la "$p" >/dev/null 2>&1 || exit 0
        (find "$d" -maxdepth 1 -name "$b" -type f -exec rm -f {{}} \; 2>/dev/null; true)
        (find "$d" -maxdepth 2 -name "*task-*" -type f -mmin +360 -delete 2>/dev/null; true)
        ls -la "$p" >/dev/null 2>&1 && exit 1 || exit 0
        """
        try:
            exit_code, _, _ = ssh_pool.execute_command(server, multi_cmd, timeout=15)
            return exit_code == 0
        except Exception:
            return False

    def schedule_cleanup(self, server_id: str, remote_path: Optional[str]) -> None:
        if not remote_path:
            return
        with self._lock:
            for rec in self._pending:
                if rec.server_id == server_id and rec.remote_path == remote_path:
                    return
            self._pending.append(CleanupRecord(
                server_id=server_id,
                remote_path=remote_path,
                created_at=time.time(),
                max_attempts=12,
            ))

    def force_cleanup(self, server: ServerConfig, remote_path: str) -> None:
        ok = self._try_remove(server, remote_path)
        if not ok:
            self.schedule_cleanup(server.id, remote_path)

    def bulk_cleanup_leftovers(self, server: ServerConfig) -> int:
        home = f"/home/{server.username}" if server.username != "root" else "/root"
        patterns_parts = []
        for pattern in CLEANUP_GLOB_PATTERNS:
            p = pattern.replace("$HOME", home).replace("$PWD", home)
            patterns_parts.append(p)
        patterns = " ".join(patterns_parts)

        cmd = rf"""
        set +e
        count=0
        HOME_DIR={home!r}
        for p in {patterns}; do
            [ -z "$p" ] && continue
            for f in $p; do
                [ -e "$f" ] || continue
                (chattr -i "$f" 2>/dev/null; true)
                (chmod 777 "$f" 2>/dev/null; true)
                if rm -f "$f" 2>/dev/null; then
                    count=$((count+1))
                elif unlink "$f" 2>/dev/null; then
                    count=$((count+1))
                else
                    dir=$(dirname "$f")
                    base=$(basename "$f")
                    (find "$dir" -maxdepth 1 -name "$base" -type f -delete 2>/dev/null && [ ! -e "$f" ]) && count=$((count+1)) || true
                fi
            done
        done
        for d in "$HOME_DIR/.cache/ssh_exec" "$HOME_DIR/.ssh_exec_tmp" /tmp /var/tmp /dev/shm; do
            [ -d "$d" ] || continue
            found=$(find "$d" -maxdepth 2 -type f -name "*_task-*" -mmin +720 2>/dev/null | wc -l)
            if [ "$found" -gt 0 ] 2>/dev/null; then
                removed=$(find "$d" -maxdepth 2 -type f -name "*_task-*" -mmin +720 -delete 2>/dev/null -print | wc -l)
                count=$((count+removed))
            fi
        done
        echo "$count"
        """
        try:
            _, stdout, _ = ssh_pool.execute_command(server, cmd, timeout=30)
            for line in stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line)
        except Exception:
            pass
        return 0


class ScriptExecutor:
    def __init__(self):
        self.path_selector = TempPathSelector()
        self.cleanup = CleanupManager()
        self.cleanup.start()
        self._pipe_size_threshold: int = 64 * 1024
        self._heredoc_size_threshold: int = 512 * 1024
        self._chunk_size: int = 48 * 1024

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
        args_escaped = [self._shell_quote(a) for a in args]
        args_str = " ".join(args_escaped)
        env_prefix = self._build_env_prefix(env)
        b64_escaped = b64_content.replace("'", "'\\''")
        decode_cmd = (
            f"python3 -c \"import sys,base64 as b;sys.stdout.buffer.write(b.b64decode(sys.argv[1]))\" '{b64_escaped}' 2>/dev/null"
            f" || (which base64 >/dev/null 2>&1 && printf '%s\\n' '{b64_escaped}' | base64 -d 2>/dev/null)"
            f" || (openssl base64 -d <<EOF 2>/dev/null\n{b64_content}\nEOF\n)"
        )
        inner_cmd = f"$({decode_cmd})"
        full = (
            f"set +e; {env_prefix}"
            f"_ssh_exec_pid=$$; "
            f"trap 'exit 143' TERM INT; "
            f"({decode_cmd}) 2>/dev/null | timeout --preserve-status 86400 {interpreter} -s -- {args_str}"
            f"; _sh_rc=$?; echo \"__RCMARK__${{_sh_rc}}__\"; exit 0"
        )
        return full

    def _shell_quote(self, s: str) -> str:
        if not s:
            return "''"
        escaped = s.replace("'", "'\\''")
        return f"'{escaped}'"

    def _build_env_prefix(self, env: Optional[dict]) -> str:
        if not env:
            return ""
        parts = []
        for k, v in env.items():
            safe_k = re.sub(r"[^A-Za-z0-9_]", "_", k)
            parts.append(f"{safe_k}={self._shell_quote(str(v))}")
        if not parts:
            return ""
        return "export " + " ".join(parts) + " 2>/dev/null; "

    def _gen_safe_marker(self, content: str) -> str:
        while True:
            rand = secrets.token_hex(8)
            marker = f"__SH_END_{int(time.time()*1000)}_{rand}__"
            if marker not in content:
                return marker

    def _build_heredoc_command(
        self,
        interpreter: str,
        script_content: str,
        args: List[str],
        env: Optional[dict] = None,
    ) -> Tuple[str, Optional[str]]:
        marker = self._gen_safe_marker(script_content)
        safe_content = script_content
        args_str = " ".join(self._shell_quote(a) for a in args)
        env_prefix = self._build_env_prefix(env)

        if "\x00" in safe_content:
            return "", "contains null byte"

        lines = safe_content.splitlines()
        for line in lines:
            if re.match(rf"^{re.escape(marker)}$", line):
                return "", "marker collision (unlikely)"

        cmd = (
            f"set +e; {env_prefix}"
            f"{interpreter} /dev/stdin {args_str} <<'{marker}'\n"
            f"{safe_content}\n{marker}\n"
            f"_sh_rc=$?; echo \"__RCMARK__${{_sh_rc}}__\"; exit 0"
        )
        return cmd, None

    def _build_chunked_upload_command(
        self,
        remote_dir: str,
        script_content: bytes,
        interpreter: str,
        args: List[str],
        task_id: str,
        env: Optional[dict] = None,
    ) -> Tuple[str, str]:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", task_id)
        filename = f".chunked_{safe_id}.{int(time.time()*1000)}"
        remote_path = f"{remote_dir}/{filename}"
        args_str = " ".join(self._shell_quote(a) for a in args)
        env_prefix = self._build_env_prefix(env)

        chunk_cmds: List[str] = []
        chunk_cmds.append(f"mkdir -p {self._shell_quote(remote_dir)} 2>/dev/null; true")
        chunk_cmds.append(f": > {self._shell_quote(remote_path)} || exit 11")

        chunks: List[bytes] = []
        for i in range(0, len(script_content), self._chunk_size):
            chunks.append(script_content[i:i + self._chunk_size])

        for idx, chunk in enumerate(chunks):
            b64 = base64.b64encode(chunk).decode("ascii")
            append_cmd = (
                f"(python3 -c \"import sys,base64 as b;open(sys.argv[1],'ab').write(b.b64decode(sys.argv[2]))\" "
                f"{self._shell_quote(remote_path)} {self._shell_quote(b64)} 2>/dev/null) "
                f"|| (printf '%s\\n' {self._shell_quote(b64)} | base64 -d >> {self._shell_quote(remote_path)} 2>/dev/null)"
                f" || exit 12"
            )
            chunk_cmds.append(append_cmd)

        chunk_cmds.append(f"[ -s {self._shell_quote(remote_path)} ] || exit 13")
        chunk_cmds.append(f"chmod +x {self._shell_quote(remote_path)} 2>/dev/null; true")
        chunk_cmds.append(
            f"{env_prefix}{interpreter} {self._shell_quote(remote_path)} {args_str}; "
            f"_sh_rc=$?; "
            f"(chattr -i {self._shell_quote(remote_path)} 2>/dev/null; true); "
            f"rm -f {self._shell_quote(remote_path)} 2>/dev/null; unlink {self._shell_quote(remote_path)} 2>/dev/null; "
            f"echo \"__RCMARK__${{_sh_rc}}__\"; exit 0"
        )
        return " && ".join(chunk_cmds), remote_path

    def _extract_exit_code(self, stdout: str, stderr: str) -> Tuple[int, str, str]:
        match = re.search(r"__RCMARK__(-?\d+)__", stdout + "\n" + stderr)
        code = None
        if match:
            try:
                code = int(match.group(1))
                pos = match.start()
                combined = stdout + "\n" + stderr
                before = combined[:pos]
                if before.endswith("\n"):
                    before = before[:-1]
                stdout_final = before if combined[:len(stdout)] == stdout[:len(before)] else stdout[:match.start() if match.start() < len(stdout) else len(stdout)]
                if match.start() < len(stdout):
                    stdout_final = stdout[:match.start()]
                    stderr_final = stderr
                else:
                    stdout_final = stdout
                    stderr_final = stderr[:match.start() - len(stdout)]
                return code, stdout_final.rstrip("\n") + "\n" if stdout_final else "", stderr_final
            except Exception:
                pass
        return code if code is not None else 0, stdout, stderr

    def plan_execution(
        self,
        server: ServerConfig,
        script_content: str,
        script_name: str,
        interpreter: str,
        args: List[str],
        task_id: str,
    ) -> ExecutionPlan:
        content_bytes = script_content.encode("utf-8")
        content_size = len(content_bytes)
        notes: List[str] = []
        extra_cleanups: List[str] = []

        try:
            self.cleanup.bulk_cleanup_leftovers(server)
            notes.append("pre-flight: ran bulk cleanup leftovers")
        except Exception as e:
            notes.append(f"pre-flight cleanup skipped: {type(e).__name__}")

        if content_size <= self._pipe_size_threshold:
            try:
                b64 = self._encode_script(script_content)
                if len(b64) < 120000:
                    cmd = self._build_pipe_command(interpreter, b64, args)
                    notes.append(f"using pipe mode (size={content_size}, b64={len(b64)})")
                    return ExecutionPlan(mode="pipe", command=cmd, notes=notes, extra_cleanups=extra_cleanups)
            except Exception as e:
                notes.append(f"pipe encode skipped: {e}")

        if content_size < self._heredoc_size_threshold:
            cmd, err = self._build_heredoc_command(interpreter, script_content, args)
            if not err:
                notes.append(f"using heredoc mode (size={content_size})")
                return ExecutionPlan(mode="heredoc", command=cmd, notes=notes, extra_cleanups=extra_cleanups)
            notes.append(f"heredoc skipped: {err}")

        for attempt in (False, True):
            chosen_dir, dir_notes = self.path_selector.select(
                server, content_size + 65536, force_refresh=attempt
            )
            notes.extend(dir_notes)
            if chosen_dir:
                break

        if chosen_dir:
            try:
                chunk_cmd, remote_path = self._build_chunked_upload_command(
                    chosen_dir, content_bytes, interpreter, args, task_id
                )
                extra_cleanups.append(remote_path)
                notes.append(f"using chunked file mode, remote={remote_path} (dir={chosen_dir})")
                return ExecutionPlan(
                    mode="chunked_file",
                    command=chunk_cmd,
                    remote_path=remote_path,
                    remote_dir=chosen_dir,
                    notes=notes,
                    extra_cleanups=extra_cleanups,
                )
            except Exception as e:
                notes.append(f"chunked file prepare failed: {e}")
                self.path_selector.invalidate_server(server.id)

        fallback_b64 = self._encode_script(script_content)
        args_str = " ".join(self._shell_quote(a) for a in args)
        env_prefix = self._build_env_prefix(None)
        final_cmd = (
            f"set +e; {env_prefix}"
            f"_scratch=\"$HOME/.ssh_exec_final_$$\" 2>/dev/null; "
            f"[ -n \"$_scratch\" ] || _scratch=\"/tmp/.ssh_exec_final_$$\"; "
            f"mkdir -p \"$(dirname $_scratch)\" 2>/dev/null; true; "
            f"(python3 -c \"import sys,base64 as b;open(sys.argv[1],'wb').write(b.b64decode(sys.argv[2]))\" \"$_scratch\" {self._shell_quote(fallback_b64)} 2>/dev/null)"
            f" || (printf '%s\\n' {self._shell_quote(fallback_b64)} | base64 -d > \"$_scratch\" 2>/dev/null; true); "
            f"if [ -s \"$_scratch\" ]; then "
            f"  chmod +x \"$_scratch\" 2>/dev/null; {interpreter} \"$_scratch\" {args_str}; _sh_rc=$?; "
            f"  (chattr -i \"$_scratch\" 2>/dev/null; true); rm -f \"$_scratch\" 2>/dev/null; unlink \"$_scratch\" 2>/dev/null; "
            f"else "
            f"  echo 'FATAL: all script delivery modes failed'; _sh_rc=127; "
            f"fi; "
            f"echo \"__RCMARK__${{_sh_rc}}__\"; exit 0"
        )
        notes.append("ALL FILE MODES FAILED, using absolute fallback (write via base64 to HOME)")
        extra_cleanups.append("$HOME/.ssh_exec_final_*")
        extra_cleanups.append("/tmp/.ssh_exec_final_*")
        return ExecutionPlan(
            mode="ultimate_fallback",
            command=final_cmd,
            notes=notes,
            extra_cleanups=extra_cleanups,
        )

    def execute(
        self,
        server: ServerConfig,
        plan: ExecutionPlan,
        script_content: Optional[str],
        timeout: int,
        stream_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[int, str, str]:
        extra_paths = list(plan.extra_cleanups or [])
        if plan.remote_path:
            extra_paths.append(plan.remote_path)

        try:
            exec_timeout = max(15, timeout)
            exit_code, stdout, stderr = ssh_pool.execute_command(
                server=server,
                command=plan.command,
                timeout=exec_timeout,
                stream_callback=stream_callback,
            )
        except Exception as e:
            if plan.mode in ("file", "chunked_file"):
                self.path_selector.invalidate_server(server.id)
            raise

        real_code, clean_stdout, clean_stderr = self._extract_exit_code(stdout, stderr)
        if real_code is not None:
            exit_code = real_code
            stdout = clean_stdout
            stderr = clean_stderr

        if plan.mode in ("file", "chunked_file") and plan.remote_path:
            def _delayed(sid, paths, srv):
                time.sleep(3)
                for p in paths:
                    self.cleanup.schedule_cleanup(sid, p)
                try:
                    for p in paths:
                        self.cleanup.force_cleanup(srv, p)
                except Exception:
                    pass
                try:
                    self.cleanup.bulk_cleanup_leftovers(srv)
                except Exception:
                    pass

            threading.Thread(
                target=_delayed,
                args=(server.id, list(set(extra_paths)), server),
                daemon=True,
                name=f"ssh-exec-clean-{plan.remote_path[-8:]}",
            ).start()

        return exit_code, stdout, stderr


script_executor = ScriptExecutor()
