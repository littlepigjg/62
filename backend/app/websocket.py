import asyncio
import json
from typing import Dict, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect

from .core.stream import stream_manager, StreamMessage


class WebSocketManager:
    def __init__(self):
        self._active_connections: Set[WebSocket] = set()
        self._task_subscriptions: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._active_connections.discard(websocket)
        for task_id in list(self._task_subscriptions.keys()):
            self._task_subscriptions[task_id].discard(websocket)
            if not self._task_subscriptions[task_id]:
                del self._task_subscriptions[task_id]

    def subscribe_task(self, task_id: str, websocket: WebSocket) -> None:
        if task_id not in self._task_subscriptions:
            self._task_subscriptions[task_id] = set()
        self._task_subscriptions[task_id].add(websocket)

        async def _callback(msg: StreamMessage) -> None:
            try:
                await websocket.send_text(msg.to_json())
            except Exception:
                pass

        history = stream_manager.get_task_history(task_id)
        for msg in history:
            asyncio.create_task(_callback(msg))

        stream_manager.subscribe_task(task_id, _callback)

    def unsubscribe_task(self, task_id: str, websocket: WebSocket) -> None:
        if task_id in self._task_subscriptions:
            self._task_subscriptions[task_id].discard(websocket)

    async def broadcast(self, msg: StreamMessage) -> None:
        disconnected = set()
        for ws in list(self._active_connections):
            try:
                await ws.send_text(msg.to_json())
            except Exception:
                disconnected.add(ws)
        for ws in disconnected:
            self.disconnect(ws)


ws_manager = WebSocketManager()


async def handle_websocket(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        async def _global_callback(msg: StreamMessage) -> None:
            try:
                await websocket.send_text(msg.to_json())
            except Exception:
                pass

        stream_manager.subscribe_global(_global_callback)

        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                action = message.get("action")
                task_id = message.get("task_id")

                if action == "subscribe" and task_id:
                    ws_manager.subscribe_task(task_id, websocket)
                elif action == "unsubscribe" and task_id:
                    ws_manager.unsubscribe_task(task_id, websocket)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)
        stream_manager._global_subscribers.discard(_global_callback)
