import React, { useEffect, useState } from 'react';
import {
  Card,
  Row,
  Col,
  Button,
  Modal,
  Form,
  Input,
  InputNumber,
  Tag,
  Space,
  Select,
  App,
  Popconfirm,
  Tooltip,
  Empty,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ThunderboltOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
} from '@ant-design/icons';
import { serversApi } from '@/services/api';
import { useAppStore } from '@/store';
import type { ServerConfig } from '@/types';

const MachineManagement: React.FC = () => {
  const { message } = App.useApp();
  const { servers, setServers, addServer, updateServer, removeServer } = useAppStore();

  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ServerConfig | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [form] = Form.useForm();

  const fetchServers = async () => {
    setLoading(true);
    try {
      const data = await serversApi.list();
      setServers(data);
    } catch (e: any) {
      message.error('获取服务器列表失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchServers();
  }, []);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (server: ServerConfig) => {
    setEditing(server);
    form.setFieldsValue({
      ...server,
      tags: server.tags,
    });
    setModalOpen(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (editing) {
        const res = await serversApi.update(editing.id, values);
        updateServer(res);
        message.success('更新成功');
      } else {
        const res = await serversApi.create(values);
        addServer(res);
        message.success('创建成功');
      }
      setModalOpen(false);
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error('保存失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await serversApi.delete(id);
      removeServer(id);
      message.success('删除成功');
    } catch (e: any) {
      message.error('删除失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleTest = async (server: ServerConfig) => {
    setTestingId(server.id);
    try {
      const res = await serversApi.test(server.id);
      message.success(res.message || '连接成功');
    } catch (e: any) {
      message.error('连接失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setTestingId(null);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0 }}>服务器列表</h2>
          <p style={{ color: '#888', margin: '4px 0 0 0' }}>管理用于远程执行命令的SSH服务器</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          添加服务器
        </Button>
      </div>

      {servers.length === 0 && !loading ? (
        <Empty description="暂无服务器配置，点击右上角添加第一台服务器" />
      ) : (
        <Row gutter={[16, 16]}>
          {servers.map(s => (
            <Col xs={24} sm={12} md={8} lg={6} key={s.id}>
              <Card
                loading={loading}
                title={
                  <Tooltip title={s.name}>
                    <span style={{ fontWeight: 600 }}>{s.name}</span>
                  </Tooltip>
                }
                extra={
                  <Space size={4}>
                    <Button
                      size="small"
                      type="text"
                      icon={<ThunderboltOutlined />}
                      loading={testingId === s.id}
                      onClick={() => handleTest(s)}
                    />
                    <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit(s)} />
                    <Popconfirm
                      title="确定删除该服务器？"
                      onConfirm={() => handleDelete(s.id)}
                      okText="确定"
                      cancelText="取消"
                    >
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                }
              >
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  <div>
                    <div style={{ color: '#888', fontSize: 12 }}>地址</div>
                    <div style={{ fontFamily: 'monospace' }}>
                      {s.username}@{s.host}:{s.port}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: '#888', fontSize: 12 }}>认证</div>
                    <div>
                      {s.private_key ? (
                        <Tag icon={<CheckCircleFilled />} color="blue">密钥</Tag>
                      ) : s.password ? (
                        <Tag icon={<CheckCircleFilled />} color="green">密码</Tag>
                      ) : (
                        <Tag icon={<CloseCircleFilled />} color="default">未配置</Tag>
                      )}
                    </div>
                  </div>
                  {s.tags.length > 0 && (
                    <div>
                      <div style={{ color: '#888', fontSize: 12 }}>标签</div>
                      <div>
                        {s.tags.map(t => (
                          <Tag key={t}>{t}</Tag>
                        ))}
                      </div>
                    </div>
                  )}
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Modal
        title={editing ? '编辑服务器' : '添加服务器'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="服务器名称"
                name="name"
                rules={[{ required: true, message: '请输入服务器名称' }]}
              >
                <Input placeholder="例如：生产服务器-01" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="ID（可选）"
                name="id"
              >
                <Input placeholder="留空自动生成" disabled={!!editing} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={14}>
              <Form.Item
                label="主机地址"
                name="host"
                rules={[{ required: true, message: '请输入主机地址' }]}
              >
                <Input placeholder="192.168.1.100" />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item
                label="SSH端口"
                name="port"
                initialValue={22}
              >
                <InputNumber min={1} max={65535} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="root" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="密码（可选）" name="password">
                <Input.Password placeholder="留空使用密钥" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="私钥路径（可选）" name="private_key">
                <Input placeholder="~/.ssh/id_rsa" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="标签" name="tags">
            <Select
              mode="tags"
              placeholder="输入标签后回车"
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
};

export default MachineManagement;
