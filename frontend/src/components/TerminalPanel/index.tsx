import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Row,
  Col,
  Card,
  Input,
  Button,
  Checkbox,
  Select,
  Tabs,
  Tag,
  Space,
  App,
  Tooltip,
  Dropdown,
  InputNumber,
  Alert,
  Empty,
  Badge,
  Popconfirm,
  Typography,
  Divider,
} from 'antd';
import {
  PlayCircleOutlined,
  CaretDownOutlined,
  CopyOutlined,
  DeleteOutlined,
  FileTextOutlined,
  ReloadOutlined,
  SearchOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  LoadingOutlined,
  ClockCircleFilled,
  ThunderboltFilled,
} from '@ant-design/icons';
import { serversApi, executeApi, templatesApi } from '@/services/api';
import { wsService } from '@/services/websocket';
import { useAppStore } from '@/store';
import type { ServerConfig, ScriptTemplate } from '@/types';

const { TextArea } = Input;
const { Option } = Select;
const { Text, Paragraph } = Typography;

interface ServerSelectorProps {
  allServers: ServerConfig[];
  selectedIds: string[];
  onSelectChange: (ids: string[]) => void;
}

const ServerSelector: React.FC<ServerSelectorProps> = ({ allServers, selectedIds, onSelectChange }) => {
  const [searchTag, setSearchTag] = useState<string | undefined>();
  const [searchText, setSearchText] = useState('');
  const [allTags, setAllTags] = useState<string[]>([]);

  useEffect(() => {
    serversApi.tags().then(setAllTags).catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    return allServers.filter(s => {
      const matchTag = !searchTag || s.tags.includes(searchTag);
      const matchText =
        !searchText ||
        s.name.toLowerCase().includes(searchText.toLowerCase()) ||
        s.host.toLowerCase().includes(searchText.toLowerCase());
      return matchTag && matchText;
    });
  }, [allServers, searchTag, searchText]);

  const allSelectedInView = filtered.length > 0 && filtered.every(s => selectedIds.includes(s.id));
  const toggleSelectAll = () => {
    if (allSelectedInView) {
      onSelectChange(selectedIds.filter(id => !filtered.find(s => s.id === id)));
    } else {
      const merged = new Set([...selectedIds, ...filtered.map(s => s.id)]);
      onSelectChange(Array.from(merged));
    }
  };

  return (
    <Card
      title={
        <Space>
          <span>选择目标服务器</span>
          <Tag color={selectedIds.length > 0 ? 'blue' : 'default'}>
            已选 {selectedIds.length} / {allServers.length}
          </Tag>
        </Space>
      }
      extra={
        <Space>
          <Input
            size="small"
            prefix={<SearchOutlined />}
            placeholder="搜索名称/地址"
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            style={{ width: 160 }}
          />
          <Select
            size="small"
            placeholder="按标签过滤"
            allowClear
            value={searchTag}
            onChange={setSearchTag}
            style={{ width: 140 }}
          >
            {allTags.map(t => (
              <Option key={t} value={t}>{t}</Option>
            ))}
          </Select>
          <Button size="small" onClick={toggleSelectAll}>
            {allSelectedInView ? '取消全选' : '全选当前'}
          </Button>
        </Space>
      }
      style={{ height: '100%' }}
      bodyStyle={{ padding: 12, maxHeight: 280, overflowY: 'auto' }}
    >
      {filtered.length === 0 ? (
        <Empty description="没有匹配的服务器" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Checkbox.Group
          style={{ width: '100%' }}
          value={selectedIds}
          onChange={(vals) => onSelectChange(vals as string[])}
        >
          <Space direction="vertical" style={{ width: '100%' }} size={2}>
            {filtered.map(s => (
              <Checkbox key={s.id} value={s.id} style={{ width: '100%', marginRight: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                  <span>
                    <Text strong>{s.name}</Text>
                    <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                      {s.username}@{s.host}:{s.port}
                    </Text>
                  </span>
                  <span>
                    {s.tags.map(t => (
                      <Tag key={t} style={{ margin: 0, marginLeft: 4 }}>{t}</Tag>
                    ))}
                  </span>
                </div>
              </Checkbox>
            ))}
          </Space>
        </Checkbox.Group>
      )}
    </Card>
  );
};

interface OutputTabProps {
  taskId: string;
  serverId: string;
  serverName: string;
  stdout: string;
  stderr: string;
  status: string;
  exitCode: number | null;
  onClear?: () => void;
}

const OutputTab: React.FC<OutputTabProps> = ({ taskId, serverName, stdout, stderr, status, exitCode, onClear }) => {
  const outputRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [stdout, stderr]);

  const copyOutput = async () => {
    try {
      await navigator.clipboard.writeText(stdout + stderr);
      App.useApp().message.success('已复制到剪贴板');
    } catch {}
  };

  const statusBadge = () => {
    switch (status) {
      case 'pending':
        return <Badge status="default" text={<><ClockCircleFilled /> 等待中</>} />;
      case 'running':
        return <Badge status="processing" text={<><LoadingOutlined /> 执行中</>} />;
      case 'success':
        return <Badge status="success" text={<><CheckCircleFilled /> 成功 (exit={exitCode})</>} />;
      case 'failed':
        return <Badge status="warning" text={<><CloseCircleFilled /> 失败 (exit={exitCode})</>} />;
      case 'error':
        return <Badge status="error" text={<><CloseCircleFilled /> 执行错误</>} />;
      default:
        return <Badge status="default" text={status} />;
    }
  };

  return (
    <div className="output-tab">
      <div className="output-header">
        <Space>
          <Text style={{ color: '#fff' }}>{serverName}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            task: {taskId.slice(-12)}
          </Text>
          {statusBadge()}
        </Space>
        <Space>
          <Button size="small" icon={<CopyOutlined />} onClick={copyOutput}>复制</Button>
          {onClear && (
            <Button size="small" danger icon={<DeleteOutlined />} onClick={onClear}>清除</Button>
          )}
        </Space>
      </div>
      <div className="output-content" ref={outputRef}>
        {stdout.split('\n').map((line, i) => (
          <div key={`o-${i}`} className="stdout" style={{ color: '#d4d4d4' }}>{line}</div>
        ))}
        {stderr.split('\n').map((line, i) => (
          <div key={`e-${i}`} className="stderr" style={{ color: '#f48771' }}>{line}</div>
        ))}
        {!stdout && !stderr && status === 'pending' && (
          <Text type="secondary" style={{ opacity: 0.6 }}>等待执行...</Text>
        )}
      </div>
    </div>
  );
};

const TerminalPanel: React.FC = () => {
  const { message } = App.useApp();

  const {
    servers,
    setServers,
    selectedServerIds,
    setSelectedServerIds,
    templates,
    setTemplates,
    taskOutputs,
    addActiveTasks,
    clearTask,
  } = useAppStore();

  const [mode, setMode] = useState<'command' | 'script'>('command');
  const [command, setCommand] = useState('ls -la');
  const [scriptContent, setScriptContent] = useState('#!/bin/bash\necho "Hello from script"\n');
  const [scriptName, setScriptName] = useState('script.sh');
  const [interpreter, setInterpreter] = useState('bash');
  const [timeout, setTimeout] = useState(300);
  const [executing, setExecuting] = useState(false);
  const [activeTaskKey, setActiveTaskKey] = useState<string | null>(null);

  useEffect(() => {
    serversApi.list().then(setServers).catch(() => {});
    templatesApi.list().then(setTemplates).catch(() => {});
  }, []);

  const outputList = useMemo(() => Array.from(taskOutputs.entries()), [taskOutputs]);

  useEffect(() => {
    if (!activeTaskKey && outputList.length > 0) {
      setActiveTaskKey(outputList[0][0]);
    }
    if (activeTaskKey && !taskOutputs.has(activeTaskKey) && outputList.length > 0) {
      setActiveTaskKey(outputList[0][0]);
    }
  }, [outputList.length, activeTaskKey]);

  useEffect(() => {
    outputList.forEach(([taskId]) => wsService.subscribe(taskId));
    return () => {
      outputList.forEach(([taskId]) => wsService.unsubscribe(taskId));
    };
  }, [outputList.map(o => o[0]).join(',')]);

  const handleExecute = async () => {
    if (selectedServerIds.length === 0) {
      message.warning('请先选择目标服务器');
      return;
    }

    setExecuting(true);
    try {
      let results;
      if (mode === 'command') {
        if (!command.trim()) {
          message.warning('请输入要执行的命令');
          return;
        }
        results = await executeApi.command({
          server_ids: selectedServerIds,
          command,
          timeout,
        });
      } else {
        if (!scriptContent.trim()) {
          message.warning('请输入脚本内容');
          return;
        }
        results = await executeApi.script({
          server_ids: selectedServerIds,
          script_content: scriptContent,
          script_name: scriptName || 'script.sh',
          interpreter,
          timeout,
        });
      }

      addActiveTasks(results);
      const firstId = results[0]?.task_id;
      if (firstId) {
        setActiveTaskKey(firstId);
        results.forEach(r => wsService.subscribe(r.task_id));
      }
      message.success(`已提交 ${results.length} 个执行任务`);
    } catch (e: any) {
      message.error('执行失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setExecuting(false);
    }
  };

  const applyTemplate = (tpl: ScriptTemplate) => {
    setMode('script');
    setScriptContent(tpl.script_content);
    setInterpreter(tpl.interpreter);
    setScriptName(tpl.name.endsWith('.sh') ? tpl.name : `${tpl.name}.sh`);
    message.success(`已加载模板：${tpl.name}`);
  };

  const templateMenuItems = templates.length > 0
    ? templates.map(t => ({
        key: t.id,
        label: (
          <div>
            <div style={{ fontWeight: 500 }}>{t.name}</div>
            <div style={{ fontSize: 11, color: '#999' }}>{t.description || t.interpreter}</div>
          </div>
        ),
      }))
    : [{ key: 'empty', disabled: true, label: '暂无模板，请去脚本库添加' }];

  return (
    <div className="execute-panel">
      <Row gutter={16}>
        <Col xs={24} lg={10}>
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <ServerSelector
              allServers={servers}
              selectedIds={selectedServerIds}
              onSelectChange={setSelectedServerIds}
            />

            <Card
              title={
                <Space>
                  <ThunderboltFilled style={{ color: '#1677ff' }} />
                  任务配置
                </Space>
              }
              extra={
                <Space>
                  <Text type="secondary" style={{ fontSize: 12 }}>超时:</Text>
                  <InputNumber
                    min={5}
                    max={3600}
                    value={timeout}
                    onChange={(v) => setTimeout(v || 300)}
                    addonAfter="秒"
                    size="small"
                    style={{ width: 110 }}
                  />
                </Space>
              }
            >
              <Tabs
                activeKey={mode}
                onChange={setMode as any}
                size="small"
                items={[
                  {
                    key: 'command',
                    label: '单条命令',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }} size="middle">
                        <TextArea
                          value={command}
                          onChange={e => setCommand(e.target.value)}
                          placeholder="输入要执行的命令，例如：uname -a; uptime"
                          autoSize={{ minRows: 4, maxRows: 10 }}
                          style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: 13 }}
                          onPressEnter={(e) => {
                            if (e.ctrlKey || e.metaKey) handleExecute();
                          }}
                        />
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Space>
                            <Button onClick={() => setCommand('uname -a')}>uname</Button>
                            <Button onClick={() => setCommand('uptime; free -h; df -h')}>系统状态</Button>
                            <Button onClick={() => setCommand('ps aux --sort=-%mem | head -10')}>Top进程</Button>
                            <Button onClick={() => setCommand('')} danger>清空</Button>
                          </Space>
                          <Text type="secondary" style={{ fontSize: 12 }}>Ctrl+Enter 快速执行</Text>
                        </div>
                      </Space>
                    ),
                  },
                  {
                    key: 'script',
                    label: '执行脚本',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }} size="middle">
                        <Space>
                          <span style={{ fontSize: 12 }}>脚本名:</span>
                          <Input
                            size="small"
                            value={scriptName}
                            onChange={e => setScriptName(e.target.value)}
                            style={{ width: 160 }}
                          />
                          <span style={{ fontSize: 12 }}>解释器:</span>
                          <Select
                            size="small"
                            value={interpreter}
                            onChange={setInterpreter}
                            style={{ width: 120 }}
                          >
                            <Option value="bash">bash</Option>
                            <Option value="sh">sh</Option>
                            <Option value="python3">python3</Option>
                            <Option value="python">python</Option>
                            <Option value="zsh">zsh</Option>
                          </Select>
                          <Dropdown
                            menu={{
                              items: templateMenuItems,
                              onClick: ({ key }) => {
                                const tpl = templates.find(t => t.id === key);
                                if (tpl) applyTemplate(tpl);
                              },
                            }}
                          >
                            <Button size="small" icon={<FileTextOutlined />}>
                              从模板导入 <CaretDownOutlined />
                            </Button>
                          </Dropdown>
                        </Space>
                        <TextArea
                          className="script-editor"
                          value={scriptContent}
                          onChange={e => setScriptContent(e.target.value)}
                          placeholder="输入脚本内容"
                          autoSize={{ minRows: 8, maxRows: 12 }}
                          style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: 13 }}
                        />
                      </Space>
                    ),
                  },
                ]}
              />

              <Divider style={{ margin: '16px 0' }} />

              <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                <Button onClick={() => {
                  const running = outputList.some(([, o]) => o.status === 'running' || o.status === 'pending');
                  if (!running && outputList.length > 0) {
                    outputList.forEach(([id]) => clearTask(id));
                    setActiveTaskKey(null);
                    message.success('已清空输出');
                  } else if (running) {
                    message.warning('仍有任务执行中，请等待完成后清除');
                  }
                }}>
                  清空全部输出
                </Button>
                <Button
                  type="primary"
                  size="large"
                  icon={<PlayCircleOutlined />}
                  onClick={handleExecute}
                  loading={executing}
                  disabled={selectedServerIds.length === 0}
                >
                  执行任务 ({selectedServerIds.length}台)
                </Button>
              </Space>
            </Card>
          </Space>
        </Col>

        <Col xs={24} lg={14}>
          <Card
            title={
              <Space>
                <span>实时输出面板</span>
                {outputList.length > 0 && (
                  <Tag color="blue">{outputList.length} 个任务</Tag>
                )}
              </Space>
            }
            style={{ minHeight: 560 }}
            bodyStyle={{ padding: 0 }}
          >
            {outputList.length === 0 ? (
              <div style={{ padding: 48 }}>
                <Empty
                  description={
                    <Paragraph style={{ margin: 0 }}>
                      <Text type="secondary">选择服务器后执行命令或脚本，实时输出将在此展示</Text>
                    </Paragraph>
                  }
                />
              </div>
            ) : (
              <div style={{ background: '#1e1e1e', minHeight: 520 }}>
                <Tabs
                  activeKey={activeTaskKey || undefined}
                  onChange={setActiveTaskKey}
                  tabPosition="left"
                  tabBarStyle={{
                    background: '#252526',
                    minWidth: 200,
                    paddingTop: 8,
                    margin: 0,
                    borderRight: '1px solid #3c3c3c',
                  }}
                  style={{ height: 520 }}
                  items={outputList.map(([taskId, output]) => ({
                    key: taskId,
                    label: (
                      <div style={{ paddingRight: 12, minWidth: 160 }}>
                        <div style={{
                          color: output.status === 'success' ? '#73d13d'
                            : output.status === 'failed' || output.status === 'error' ? '#ff7875'
                            : output.status === 'running' ? '#40a9ff'
                            : '#ccc',
                          fontSize: 12,
                          fontWeight: 500,
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}>
                          {output.status === 'running' && <LoadingOutlined style={{ marginRight: 4 }} />}
                          {output.status === 'success' && <CheckCircleFilled style={{ marginRight: 4 }} />}
                          {(output.status === 'failed' || output.status === 'error') && (
                            <CloseCircleFilled style={{ marginRight: 4 }} />
                          )}
                          {output.status === 'pending' && <ClockCircleFilled style={{ marginRight: 4 }} />}
                          {output.serverName}
                        </div>
                        <div style={{
                          color: '#666',
                          fontSize: 11,
                          fontFamily: 'monospace',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}>
                          {taskId.slice(-12)}
                        </div>
                      </div>
                    ),
                    children: (
                      <OutputTab
                        taskId={taskId}
                        serverId={output.serverId}
                        serverName={output.serverName}
                        stdout={output.stdout}
                        stderr={output.stderr}
                        status={output.status}
                        exitCode={output.exitCode}
                        onClear={() => { clearTask(taskId); }}
                      />
                    ),
                  }))}
                />
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default TerminalPanel;
