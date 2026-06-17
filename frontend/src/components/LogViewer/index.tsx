import React, { useEffect, useState } from 'react';
import {
  Card,
  Table,
  Tag,
  Space,
  Button,
  Modal,
  Select,
  App,
  Typography,
  Input,
  Empty,
  Tooltip,
} from 'antd';
import {
  ReloadOutlined,
  SearchOutlined,
  EyeOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  LoadingOutlined,
  ClockCircleFilled,
  DownloadOutlined,
} from '@ant-design/icons';
import { logsApi, serversApi } from '@/services/api';
import type { LogEntry, ServerConfig } from '@/types';

const { Option } = Select;
const { Text, Title } = Typography;

const StatusTag: React.FC<{ status: string }> = ({ status }) => {
  switch (status) {
    case 'success':
      return <Tag icon={<CheckCircleFilled />} color="success">成功</Tag>;
    case 'failed':
      return <Tag icon={<CloseCircleFilled />} color="warning">失败</Tag>;
    case 'error':
      return <Tag icon={<CloseCircleFilled />} color="error">错误</Tag>;
    case 'running':
      return <Tag icon={<LoadingOutlined />} color="processing">执行中</Tag>;
    case 'pending':
      return <Tag icon={<ClockCircleFilled />} color="default">等待中</Tag>;
    default:
      return <Tag>{status}</Tag>;
  }
};

const LogViewer: React.FC = () => {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [servers, setServers] = useState<ServerConfig[]>([]);
  const [filterDate, setFilterDate] = useState<string | undefined>();
  const [filterServerId, setFilterServerId] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');
  const [viewingLog, setViewingLog] = useState<LogEntry | null>(null);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [l, d, s] = await Promise.all([
        logsApi.list({ date: filterDate, server_id: filterServerId, limit: 200 }),
        logsApi.dates(),
        serversApi.list(),
      ]);
      setLogs(l);
      setDates(d);
      setServers(s);
    } catch (e) {
      message.error('加载日志失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [filterDate, filterServerId]);

  const filtered = logs.filter(l => {
    if (!keyword) return true;
    const kw = keyword.toLowerCase();
    return (
      l.command.toLowerCase().includes(kw) ||
      l.output.toLowerCase().includes(kw) ||
      l.task_id.toLowerCase().includes(kw) ||
      l.server_name.toLowerCase().includes(kw)
    );
  });

  const downloadLog = (log: LogEntry) => {
    const content = `Task ID: ${log.task_id}
Server: ${log.server_name} (${log.server_id})
Command: ${log.command}
${log.script_name ? `Script: ${log.script_name}\n` : ''}Start: ${log.start_time}
End: ${log.end_time}
Status: ${log.status}, Exit Code: ${log.exit_code}

=== OUTPUT ===
${log.output}
`;
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `log_${log.task_id}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const columns = [
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: string) => <StatusTag status={s} />,
    },
    {
      title: '服务器',
      dataIndex: 'server_name',
      key: 'server_name',
      width: 140,
      render: (name: string, r: LogEntry) => (
        <Tooltip title={r.server_id}>
          <Text strong>{name}</Text>
        </Tooltip>
      ),
    },
    {
      title: '执行命令/脚本',
      dataIndex: 'command',
      key: 'command',
      ellipsis: true,
      render: (cmd: string, r: LogEntry) => (
        <div>
          {r.script_name && <Tag color="purple" style={{ marginRight: 6 }}>{r.script_name}</Tag>}
          <Text code style={{ fontSize: 12 }}>{cmd}</Text>
        </div>
      ),
    },
    {
      title: '开始时间',
      dataIndex: 'start_time',
      key: 'start_time',
      width: 170,
      render: (t: string) => <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{t.replace('T', ' ')}</Text>,
    },
    {
      title: '退出码',
      dataIndex: 'exit_code',
      key: 'exit_code',
      width: 80,
      align: 'center' as const,
      render: (c: number | null) => c === null ? '-' : (
        <Tag color={c === 0 ? 'green' : 'red'} style={{ margin: 0 }}>
          {c}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: any, r: LogEntry) => (
        <Space size="small">
          <Button size="small" icon={<EyeOutlined />} onClick={() => setViewingLog(r)}>
            查看
          </Button>
          <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadLog(r)}>
            下载
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>执行历史日志</Title>
          <Text type="secondary">查看所有历史执行记录及其完整输出</Text>
        </div>
        <Space wrap>
          <Select
            placeholder="选择日期"
            allowClear
            style={{ width: 150 }}
            value={filterDate}
            onChange={setFilterDate}
          >
            {dates.map(d => <Option key={d} value={d}>{d}</Option>)}
          </Select>
          <Select
            placeholder="按服务器筛选"
            allowClear
            style={{ width: 180 }}
            value={filterServerId}
            onChange={setFilterServerId}
            showSearch
            optionFilterProp="label"
          >
            {servers.map(s => (
              <Option key={s.id} value={s.id} label={s.name}>{s.name} ({s.host})</Option>
            ))}
          </Select>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索命令/输出"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            style={{ width: 200 }}
            allowClear
          />
          <Button icon={<ReloadOutlined />} onClick={fetchData} loading={loading}>刷新</Button>
        </Space>
      </div>

      <Card bodyStyle={{ padding: 0 }}>
        <Table
          rowKey="task_id"
          loading={loading}
          dataSource={filtered}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条记录` }}
          locale={{ emptyText: <Empty description="暂无执行记录" /> }}
        />
      </Card>

      <Modal
        title={
          <Space>
            <span>日志详情</span>
            <StatusTag status={viewingLog?.status || ''} />
            {viewingLog && (
              <Tag>{viewingLog.server_name}</Tag>
            )}
          </Space>
        }
        open={!!viewingLog}
        onCancel={() => setViewingLog(null)}
        footer={
          <Space>
            {viewingLog && (
              <Button icon={<DownloadOutlined />} onClick={() => downloadLog(viewingLog)}>
                下载日志
              </Button>
            )}
            <Button onClick={() => setViewingLog(null)}>关闭</Button>
          </Space>
        }
        width={900}
        destroyOnClose
      >
        {viewingLog && (
          <div className="log-viewer">
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>任务ID：</Text>
                  <Text code style={{ fontSize: 12 }}>{viewingLog.task_id}</Text>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>日志文件：</Text>
                  <Text code style={{ fontSize: 12 }}>{viewingLog.log_file}</Text>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>开始时间：</Text>
                  <Text>{viewingLog.start_time}</Text>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>结束时间：</Text>
                  <Text>{viewingLog.end_time}</Text>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>退出码：</Text>
                  <Tag color={viewingLog.exit_code === 0 ? 'green' : 'red'}>
                    {viewingLog.exit_code ?? 'N/A'}
                  </Tag>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>执行命令：</Text>
                </div>
              </div>
              <div>
                <pre style={{
                  margin: 0,
                  padding: 8,
                  background: '#f5f5f5',
                  borderRadius: 4,
                  fontFamily: 'Consolas, monospace',
                  fontSize: 12,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}>
                  {viewingLog.command}
                </pre>
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>输出内容：</Text>
                <pre className="log-output">
                  {viewingLog.output || '(无输出)'}
                </pre>
              </div>
            </Space>
          </div>
        )}
      </Modal>
    </Space>
  );
};

export default LogViewer;
