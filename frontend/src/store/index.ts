import { create } from 'zustand';
import type { ServerConfig, ExecutionResult, ScriptTemplate, StreamMessage } from '../types';

interface TaskOutput {
  taskId: string;
  serverId: string;
  serverName: string;
  stdout: string;
  stderr: string;
  status: string;
  exitCode: number | null;
}

interface AppState {
  servers: ServerConfig[];
  selectedServerIds: string[];
  templates: ScriptTemplate[];
  activeTasks: Map<string, ExecutionResult>;
  taskOutputs: Map<string, TaskOutput>;
  currentTab: string;

  setServers: (servers: ServerConfig[]) => void;
  addServer: (server: ServerConfig) => void;
  updateServer: (server: ServerConfig) => void;
  removeServer: (id: string) => void;
  setSelectedServerIds: (ids: string[]) => void;

  setTemplates: (templates: ScriptTemplate[]) => void;
  addTemplate: (tpl: ScriptTemplate) => void;
  updateTemplate: (tpl: ScriptTemplate) => void;
  removeTemplate: (id: string) => void;

  addActiveTasks: (tasks: ExecutionResult[]) => void;
  updateTask: (task: ExecutionResult) => void;
  handleStreamMessage: (msg: StreamMessage) => void;
  clearTask: (taskId: string) => void;

  setCurrentTab: (tab: string) => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  servers: [],
  selectedServerIds: [],
  templates: [],
  activeTasks: new Map(),
  taskOutputs: new Map(),
  currentTab: 'execute',

  setServers: (servers) => set({ servers }),
  addServer: (server) => set(state => {
    const exists = state.servers.some(s => s.id === server.id);
    return {
      servers: exists
        ? state.servers.map(s => s.id === server.id ? server : s)
        : [...state.servers, server],
    };
  }),
  updateServer: (server) => set(state => ({
    servers: state.servers.map(s => s.id === server.id ? server : s),
  })),
  removeServer: (id) => set(state => ({
    servers: state.servers.filter(s => s.id !== id),
    selectedServerIds: state.selectedServerIds.filter(sid => sid !== id),
  })),
  setSelectedServerIds: (ids) => set({ selectedServerIds: ids }),

  setTemplates: (templates) => set({ templates }),
  addTemplate: (tpl) => set(state => {
    const exists = state.templates.some(t => t.id === tpl.id);
    return {
      templates: exists
        ? state.templates.map(t => t.id === tpl.id ? tpl : t)
        : [tpl, ...state.templates],
    };
  }),
  updateTemplate: (tpl) => set(state => ({
    templates: state.templates.map(t => t.id === tpl.id ? tpl : t),
  })),
  removeTemplate: (id) => set(state => ({
    templates: state.templates.filter(t => t.id !== id),
  })),

  addActiveTasks: (tasks) => set(state => {
    const newActive = new Map(state.activeTasks);
    const newOutputs = new Map(state.taskOutputs);
    tasks.forEach(t => {
      newActive.set(t.task_id, t);
      if (!newOutputs.has(t.task_id)) {
        newOutputs.set(t.task_id, {
          taskId: t.task_id,
          serverId: t.server_id,
          serverName: t.server_name,
          stdout: '',
          stderr: '',
          status: t.status,
          exitCode: null,
        });
      }
    });
    return { activeTasks: newActive, taskOutputs: newOutputs };
  }),
  updateTask: (task) => set(state => {
    const newActive = new Map(state.activeTasks);
    newActive.set(task.task_id, task);
    return { activeTasks: newActive };
  }),
  handleStreamMessage: (msg) => set(state => {
    const outputs = new Map(state.taskOutputs);
    const active = new Map(state.activeTasks);
    const key = msg.task_id;

    const existing = outputs.get(key) || {
      taskId: msg.task_id,
      serverId: msg.server_id,
      serverName: msg.server_name,
      stdout: '',
      stderr: '',
      status: '',
      exitCode: null,
    };

    if (msg.type === 'output') {
      if (msg.stream === 'stdout') {
        existing.stdout += msg.content;
      } else if (msg.stream === 'stderr') {
        existing.stderr += msg.content;
      }
    } else if (msg.type === 'status') {
      existing.status = msg.status;
      existing.exitCode = msg.exit_code;

      const task = active.get(key);
      if (task) {
        task.status = msg.status as any;
        task.exit_code = msg.exit_code;
        active.set(key, { ...task });
      }
    }

    outputs.set(key, { ...existing });
    return { taskOutputs: outputs, activeTasks: active };
  }),
  clearTask: (taskId) => set(state => {
    const outputs = new Map(state.taskOutputs);
    const active = new Map(state.activeTasks);
    outputs.delete(taskId);
    active.delete(taskId);
    return { taskOutputs: outputs, activeTasks: active };
  }),

  setCurrentTab: (tab) => set({ currentTab: tab }),
}));
