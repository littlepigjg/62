import type { StreamMessage } from '../types';

type MessageHandler = (msg: StreamMessage) => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  private handlers: Set<MessageHandler> = new Set();
  private subscriptions: Set<string> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private url: string;

  constructor() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.url = `${protocol}//${window.location.host}/ws`;
  }

  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) return;

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        console.log('[WS] Connected');
        this.subscriptions.forEach(tid => this.subscribe(tid));
      };

      this.ws.onmessage = (event) => {
        try {
          const msg: StreamMessage = JSON.parse(event.data);
          this.handlers.forEach(h => {
            try { h(msg); } catch (e) { console.error(e); }
          });
        } catch (e) {
          console.error('[WS] Parse error:', e);
        }
      };

      this.ws.onerror = (e) => {
        console.error('[WS] Error:', e);
      };

      this.ws.onclose = () => {
        console.log('[WS] Disconnected, reconnecting in 3s...');
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this.connect(), 3000);
      };
    } catch (e) {
      console.error('[WS] Connect error:', e);
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  subscribe(taskId: string) {
    this.subscriptions.add(taskId);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'subscribe', task_id: taskId }));
    }
  }

  unsubscribe(taskId: string) {
    this.subscriptions.delete(taskId);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'unsubscribe', task_id: taskId }));
    }
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
}

export const wsService = new WebSocketService();
