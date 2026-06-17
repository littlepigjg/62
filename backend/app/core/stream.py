import asyncio
import json
import threading
from typing import Dict, List, Optional, Callable, Set
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class StreamMessage:
    type: str
    task_id: str
    server_id: str
    server_name: str
    stream: str = ""
    content: str = ""
    exit_code: Optional[int] = None
    status: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "server_id": self.server_id,
            "server_name": self.server_name,
            "stream": self.stream,
            "content": self.content,
            "exit_code": self.exit_code,
            "status": self.status,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class StreamManager:
    def __init__(self):
        self._subscribers: Dict[str, Set[Callable]] = {}
        self._global_subscribers: Set[Callable] = set()
        self._lock = threading.Lock()
        self._task_buffers: Dict[str, List[StreamMessage]] = {}

    def subscribe_task(self, task_id: str, callback: Callable) -> None:
        with self._lock:
            if task_id not in self._subscribers:
                self._subscribers[task_id] = set()
            self._subscribers[task_id].add(callback)
            if task_id in self._task_buffers:
                for msg in self._task_buffers[task_id]:
                    asyncio.create_task(callback(msg))

    def subscribe_global(self, callback: Callable) -> None:
        with self._lock:
            self._global_subscribers.add(callback)

    def unsubscribe_task(self, task_id: str, callback: Callable) -> None:
        with self._lock:
            if task_id in self._subscribers:
                self._subscribers[task_id].discard(callback)

    def unsubscribe_global(self, callback: Callable) -> None:
        with self._lock:
            self._global_subscribers.discard(callback)

    def _buffer_message(self, task_id: str, msg: StreamMessage) -> None:
        if task_id not in self._task_buffers:
            self._task_buffers[task_id] = []
        self._task_buffers[task_id].append(msg)
        if len(self._task_buffers[task_id]) > 1000:
            self._task_buffers[task_id] = self._task_buffers[task_id][-500:]

    def _cleanup_buffer(self, task_id: str) -> None:
        def _cleanup():
            import time
            time.sleep(3600)
            with self._lock:
                self._task_buffers.pop(task_id, None)
        t = threading.Thread(target=_cleanup, daemon=True)
        t.start()

    async def _dispatch(self, msg: StreamMessage) -> None:
        callbacks = set()
        with self._lock:
            if msg.task_id in self._subscribers:
                callbacks.update(self._subscribers[msg.task_id])
            callbacks.update(self._global_subscribers)

        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(msg)
                else:
                    cb(msg)
            except Exception:
                pass

    def publish_output(self, task_id: str, server_id: str, server_name: str, stream: str, content: str) -> None:
        msg = StreamMessage(
            type="output",
            task_id=task_id,
            server_id=server_id,
            server_name=server_name,
            stream=stream,
            content=content,
        )
        with self._lock:
            self._buffer_message(task_id, msg)
        asyncio.run_coroutine_threadsafe(self._dispatch(msg), asyncio.get_event_loop_policy().get_event_loop())

    def publish_status(self, task_id: str, server_id: str, server_name: str, status: str, exit_code: Optional[int] = None) -> None:
        msg = StreamMessage(
            type="status",
            task_id=task_id,
            server_id=server_id,
            server_name=server_name,
            status=status,
            exit_code=exit_code,
        )
        with self._lock:
            self._buffer_message(task_id, msg)
            if status in ("success", "failed", "error"):
                self._cleanup_buffer(task_id)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self._dispatch(msg), loop)
        except RuntimeError:
            pass

    def get_task_history(self, task_id: str) -> List[StreamMessage]:
        with self._lock:
            return list(self._task_buffers.get(task_id, []))


stream_manager = StreamManager()
