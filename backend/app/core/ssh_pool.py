import asyncio
import threading
import time
from typing import Dict, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field
from collections import deque
import paramiko

from ..config import ServerConfig, settings


@dataclass
class SSHConnection:
    server_id: str
    client: paramiko.SSHClient
    last_used: float = field(default_factory=time.time)
    in_use: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self) -> bool:
        with self._lock:
            if self.in_use:
                return False
            self.in_use = True
            self.last_used = time.time()
            return True

    def release(self) -> None:
        with self._lock:
            self.in_use = False
            self.last_used = time.time()

    def is_alive(self) -> bool:
        try:
            transport = self.client.get_transport()
            if transport and transport.is_active():
                transport.send_ignore()
                return True
        except Exception:
            pass
        return False

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


class SSHConnectionPool:
    def __init__(self, max_size: Optional[int] = None):
        self.max_size = max_size or settings.ssh_pool_size
        self._pools: Dict[str, deque] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._total_connections: int = 0

    def _get_or_create_pool(self, server_id: str) -> Tuple[deque, threading.Lock]:
        with self._global_lock:
            if server_id not in self._pools:
                self._pools[server_id] = deque()
                self._locks[server_id] = threading.Lock()
            return self._pools[server_id], self._locks[server_id]

    def _create_ssh_client(self, server: ServerConfig) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: Dict[str, Any] = {
            "hostname": server.host,
            "port": server.port,
            "username": server.username,
            "timeout": settings.ssh_timeout,
            "banner_timeout": settings.ssh_timeout,
            "auth_timeout": settings.ssh_timeout,
        }

        key_file = server.key_file
        if key_file:
            connect_kwargs["key_filename"] = key_file
        elif server.password:
            connect_kwargs["password"] = server.password
        else:
            connect_kwargs["look_for_keys"] = True
            connect_kwargs["allow_agent"] = True

        client.connect(**connect_kwargs)
        return client

    def acquire(self, server: ServerConfig) -> SSHConnection:
        pool, lock = self._get_or_create_pool(server.id)

        with lock:
            while pool:
                conn = pool.popleft()
                if conn.acquire():
                    if conn.is_alive():
                        return conn
                    else:
                        conn.close()
                        self._total_connections -= 1

        if self._total_connections >= self.max_size:
            self._cleanup_idle()

        client = self._create_ssh_client(server)
        conn = SSHConnection(server_id=server.id, client=client)
        conn.acquire()
        with self._global_lock:
            self._total_connections += 1
        return conn

    def release(self, conn: SSHConnection, keep: bool = True) -> None:
        conn.release()
        if not keep:
            conn.close()
            with self._global_lock:
                self._total_connections -= 1
            return

        pool, lock = self._get_or_create_pool(conn.server_id)
        with lock:
            if conn.is_alive():
                pool.append(conn)
            else:
                conn.close()
                with self._global_lock:
                    self._total_connections -= 1

    def _cleanup_idle(self, max_idle_seconds: int = 300) -> None:
        now = time.time()
        with self._global_lock:
            for server_id, pool in list(self._pools.items()):
                lock = self._locks[server_id]
                with lock:
                    kept = []
                    while pool:
                        conn = pool.popleft()
                        if not conn.in_use and (now - conn.last_used) > max_idle_seconds:
                            conn.close()
                            self._total_connections -= 1
                        else:
                            kept.append(conn)
                    pool.extend(kept)

    def close_all(self) -> None:
        with self._global_lock:
            for server_id, pool in self._pools.items():
                lock = self._locks[server_id]
                with lock:
                    while pool:
                        conn = pool.popleft()
                        conn.close()
                        self._total_connections -= 1
            self._pools.clear()
            self._locks.clear()

    def execute_command(
        self,
        server: ServerConfig,
        command: str,
        timeout: int = 300,
        env: Optional[Dict[str, str]] = None,
        stream_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[int, str, str]:
        conn = self.acquire(server)
        try:
            client = conn.client
            transport = client.get_transport()
            session = transport.open_session()
            session.set_combine_stderr(False)
            session.settimeout(timeout)

            if env:
                for k, v in env.items():
                    try:
                        session.set_environment_variable(k, v)
                    except paramiko.ssh_exception.SSHException:
                        pass

            full_cmd = command
            if env:
                env_str = " ".join(f"{k}={v}" for k, v in env.items())
                full_cmd = f"export {env_str} 2>/dev/null; {command}"

            session.exec_command(full_cmd)

            stdout_chunks: list = []
            stderr_chunks: list = []

            def _read_stream(stream, chunks, stream_name):
                while True:
                    try:
                        data = stream.read(4096)
                        if not data:
                            break
                        decoded = data.decode("utf-8", errors="replace")
                        chunks.append(decoded)
                        if stream_callback:
                            stream_callback(stream_name, decoded)
                    except Exception:
                        break

            stdout_thread = threading.Thread(
                target=_read_stream,
                args=(session.makefile("rb"), stdout_chunks, "stdout"),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_read_stream,
                args=(session.makefile_stderr("rb"), stderr_chunks, "stderr"),
                daemon=True,
            )

            stdout_thread.start()
            stderr_thread.start()

            session.recv_exit_status()
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

            exit_code = session.exit_status
            session.close()

            return exit_code, "".join(stdout_chunks), "".join(stderr_chunks)
        finally:
            self.release(conn)

    def upload_file(
        self,
        server: ServerConfig,
        local_path: str,
        remote_path: str,
    ) -> None:
        conn = self.acquire(server)
        try:
            sftp = conn.client.open_sftp()
            try:
                sftp.put(local_path, remote_path)
            finally:
                sftp.close()
        finally:
            self.release(conn)

    def upload_content(
        self,
        server: ServerConfig,
        content: str,
        remote_path: str,
        mode: str = "0755",
    ) -> None:
        conn = self.acquire(server)
        try:
            sftp = conn.client.open_sftp()
            try:
                try:
                    parent = os.path.dirname(remote_path)
                    if parent and parent != "/":
                        try:
                            sftp.stat(parent)
                        except FileNotFoundError:
                            try:
                                sftp.mkdir(parent)
                            except Exception:
                                pass
                except Exception:
                    pass

                try:
                    with sftp.file(remote_path, "w") as f:
                        f.write(content)
                except PermissionError:
                    try:
                        sftp.remove(remote_path)
                    except Exception:
                        pass
                    with sftp.file(remote_path, "wx") as f:
                        f.write(content)

                chmod_ok = False
                try:
                    sftp.chmod(remote_path, int(mode, 8))
                    chmod_ok = True
                except Exception:
                    pass

                if not chmod_ok:
                    try:
                        for alt_mode in ["0700", "0755", "0644", "0600"]:
                            if alt_mode == mode:
                                continue
                            try:
                                sftp.chmod(remote_path, int(alt_mode, 8))
                                chmod_ok = True
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass

                if not chmod_ok:
                    try:
                        shell = conn.client.invoke_shell()
                        shell.settimeout(8)
                        shell.send(f"chmod {mode} {remote_path} 2>/dev/null; chmod +x {remote_path} 2>/dev/null\nexit\n")
                        time.sleep(0.5)
                        shell.close()
                    except Exception:
                        pass
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass
        finally:
            self.release(conn)


ssh_pool = SSHConnectionPool()
