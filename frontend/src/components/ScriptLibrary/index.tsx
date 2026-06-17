import React, { useEffect, useState } from 'react';
import {
  Card,
  Row,
  Col,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Tag,
  Space,
  App,
  Popconfirm,
  Tooltip,
  Empty,
  Typography,
  Divider,
  Alert,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  FileTextOutlined,
  CodeOutlined,
  ThunderboltOutlined,
  CalendarOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { templatesApi } from '@/services/api';
import { useAppStore } from '@/store';
import type { ScriptTemplate } from '@/types';

const { TextArea } = Input;
const { Option } = Select;
const { Text, Title, Paragraph } = Typography;

const ScriptLibrary: React.FC = () => {
  const { message } = App.useApp();
  const { templates, setTemplates, addTemplate, updateTemplate, removeTemplate } = useAppStore();

  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [viewing, setViewing] = useState<ScriptTemplate | null>(null);
  const [editing, setEditing] = useState<ScriptTemplate | null>(null);
  const [keyword, setKeyword] = useState('');
  const [filterTag, setFilterTag] = useState<string | undefined>();
  const [allTags, setAllTags] = useState<string[]>([]);
  const [form] = Form.useForm();

  const fetchTemplates = async () => {
    setLoading(true);
    try {
      const [list, tags] = await Promise.all([
        templatesApi.list(),
        templatesApi.tags(),
      ]);
      setTemplates(list);
      setAllTags(tags);
    } catch (e: any) {
      message.error('获取模板列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTemplates();
  }, []);

  const filtered = templates.filter(t => {
    const matchTag = !filterTag || t.tags.includes(filterTag);
    const kw = keyword.toLowerCase();
    const matchKw = !kw ||
      t.name.toLowerCase().includes(kw) ||
      t.description.toLowerCase().includes(kw) ||
      t.script_content.toLowerCase().includes(kw);
    return matchTag && matchKw;
  });

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ interpreter: 'bash', tags: [] });
    setModalOpen(true);
  };

  const openEdit = (tpl: ScriptTemplate) => {
    setEditing(tpl);
    form.setFieldsValue({
      name: tpl.name,
      description: tpl.description,
      script_content: tpl.script_content,
      interpreter: tpl.interpreter,
      tags: tpl.tags,
    });
    setModalOpen(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (editing) {
        const res = await templatesApi.update(editing.id, values);
        updateTemplate(res);
        message.success('模板已更新');
      } else {
        const res = await templatesApi.create(values);
        addTemplate(res);
        message.success('模板已创建');
      }
      fetchTemplates();
      setModalOpen(false);
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error('保存失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await templatesApi.delete(id);
      removeTemplate(id);
      message.success('模板已删除');
    } catch (e: any) {
      message.error('删除失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>
            <FileTextOutlined style={{ marginRight: 8 }} />
            脚本模板库
          </Title>
          <Paragraph style={{ margin: '4px 0 0 0', color: '#888' }}>
            保存常用脚本，下次一键执行
          </Paragraph>
        </div>
        <Space>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索模板"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            style={{ width: 200 }}
            allowClear
          />
          <Select
            placeholder="按标签筛选"
            allowClear
            value={filterTag}
            onChange={setFilterTag}
            style={{ width: 160 }}
          >
            {allTags.map(t => (
              <Option key={t} value={t}>{t}</Option>
            ))}
          </Select>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建模板
          </Button>
        </Space>
      </div>

      {filtered.length === 0 && !loading ? (
        <Empty
          description={
            <span>
              还没有模板，点击 <Text style={{ color: '#1677ff', fontWeight: 500 }}>新建模板</Text> 开始创建吧
            </span>
          }
        />
      ) : (
        <Row gutter={[16, 16]}>
          {filtered.map(tpl => (
            <Col xs={24} sm={12} lg={8} xl={6} key={tpl.id}>
              <Card
                loading={loading}
                hoverable
                onClick={() => setViewing(tpl)}
                title={
                  <Tooltip title={tpl.name}>
                    <Space>
                      <CodeOutlined style={{ color: '#1677ff' }} />
                      <Text strong ellipsis style={{ maxWidth: 160 }}>{tpl.name}</Text>
                    </Space>
                  </Tooltip>
                }
                extra={
                  <Space size={0} onClick={e => e.stopPropagation()}>
                    <Popconfirm
                      title="删除此模板？"
                      onConfirm={() => handleDelete(tpl.id)}
                      okText="确定"
                      cancelText="取消"
                    >
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                      />
                    </Popconfirm>
                    <Button
                      size="small"
                      type="text"
                      icon={<EditOutlined />}
                      onClick={() => openEdit(tpl)}
                    />
                  </Space>
                }
              >
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  {tpl.description && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {tpl.description}
                    </Text>
                  )}
                  <div>
                    <Text style={{ fontSize: 12, color: '#888' }}>解释器：</Text>
                    <Tag color="blue">{tpl.interpreter}</Tag>
                  </div>
                  {tpl.tags.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {tpl.tags.map(t => (
                        <Tag key={t} style={{ fontSize: 11, padding: '0 6px' }}>{t}</Tag>
                      ))}
                    </div>
                  )}
                  <Divider style={{ margin: '8px 0' }} />
                  <pre style={{
                    margin: 0,
                    padding: 8,
                    background: '#f6f8fa',
                    borderRadius: 4,
                    fontSize: 11,
                    fontFamily: 'Consolas, Monaco, monospace',
                    maxHeight: 100,
                    overflow: 'hidden',
                    color: '#333',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    opacity: 0.85,
                  }}>
                    {tpl.script_content.slice(0, 200)}
                    {tpl.script_content.length > 200 && '\n...'}
                  </pre>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#999' }}>
                    <Space>
                      <CalendarOutlined />
                      {tpl.updated_at?.slice(0, 10)}
                    </Space>
                    <Text code>{tpl.script_content.length} 字符</Text>
                  </div>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Modal
        title={editing ? '编辑脚本模板' : '新建脚本模板'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={720}
        destroyOnClose
        zIndex={1000}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Row gutter={16}>
            <Col span={14}>
              <Form.Item
                label="模板名称"
                name="name"
                rules={[{ required: true, message: '请输入模板名称' }]}
              >
                <Input placeholder="例如：服务器健康检查" />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item
                label="脚本解释器"
                name="interpreter"
                initialValue="bash"
                rules={[{ required: true }]}
              >
                <Select>
                  <Option value="bash">bash</Option>
                  <Option value="sh">sh</Option>
                  <Option value="zsh">zsh</Option>
                  <Option value="python3">python3</Option>
                  <Option value="python">python</Option>
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="描述说明" name="description">
            <Input placeholder="简要说明此脚本的用途（可选）" />
          </Form.Item>
          <Form.Item
            label="脚本内容"
            name="script_content"
            rules={[{ required: true, message: '请输入脚本内容' }]}
          >
            <TextArea
              autoSize={{ minRows: 10, maxRows: 20 }}
              placeholder="输入脚本内容，例如：&#10;#!/bin/bash&#10;echo 'Starting health check...'&#10;uptime"
              style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: 13 }}
            />
          </Form.Item>
          <Form.Item label="标签" name="tags" initialValue={[]}>
            <Select mode="tags" placeholder="输入标签后回车" style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={
          <Space>
            <FileTextOutlined />
            {viewing?.name}
          </Space>
        }
        open={!!viewing}
        onCancel={() => setViewing(null)}
        footer={
          <Space>
            <Button onClick={() => setViewing(null)}>关闭</Button>
            {viewing && (
              <Button
                type="primary"
                icon={<EditOutlined />}
                onClick={() => {
                  openEdit(viewing);
                  setViewing(null);
                }}
              >
                编辑此模板
              </Button>
            )}
          </Space>
        }
        width={800}
        destroyOnClose
        zIndex={1000}
      >
        {viewing && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {viewing.description && (
              <Alert type="info" message={viewing.description} showIcon />
            )}
            <Row gutter={12}>
              <Col span={8}>
                <Text type="secondary">解释器：</Text>
                <Tag color="blue">{viewing.interpreter}</Tag>
              </Col>
              <Col span={16}>
                <Text type="secondary">标签：</Text>
                {viewing.tags.length ? viewing.tags.map(t => <Tag key={t}>{t}</Tag>) : <Text type="secondary">无</Text>}
              </Col>
            </Row>
            <Divider style={{ margin: '8px 0' }} />
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>脚本内容：</Text>
              <pre style={{
                marginTop: 4,
                padding: 12,
                background: '#1e1e1e',
                color: '#d4d4d4',
                borderRadius: 6,
                fontFamily: 'Consolas, Monaco, monospace',
                fontSize: 13,
                lineHeight: 1.5,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                maxHeight: 400,
                overflowY: 'auto',
              }}>
                {viewing.script_content}
              </pre>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#888' }}>
              <Text>创建：{viewing.created_at?.replace('T', ' ')}</Text>
              <Text>更新：{viewing.updated_at?.replace('T', ' ')}</Text>
            </div>
          </Space>
        )}
      </Modal>
    </Space>
  );
};

export default ScriptLibrary;
