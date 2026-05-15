/**
 * 系统设置页 — 模型管理、流媒体配置、系统状态
 */

import { useEffect, useState } from 'react';
import { Tabs, Descriptions, Button, Select, Card, Tag, Switch, message, Slider, Space } from 'antd';
import {
  SettingOutlined,
  ExperimentOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { modelAPI, mockAPI, systemAPI } from '@/services/api';
import type { MockConfig } from '@/services/api';
import type { ModelStatus } from '@/types';

export function SettingsPage() {
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [targetVersion, setTargetVersion] = useState('');
  const [switching, setSwitching] = useState(false);
  const [mockRunning, setMockRunning] = useState(true);
  const [mockConfig, setMockConfig] = useState<MockConfig | null>(null);
  const [systemStatus, setSystemStatus] = useState<any>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  const fetchModelStatus = async () => {
    try {
      const res = await modelAPI.getStatus();
      setModelStatus(res.data);
    } catch {
      message.error('获取模型状态失败');
    }
  };

  useEffect(() => {
    fetchModelStatus();
    mockAPI.getStatus().then(res => setMockRunning(res.data.running)).catch(() => {});
    mockAPI.getConfig().then(res => setMockConfig(res.data)).catch(() => {});
  }, []);

  useEffect(() => {
    systemAPI.getStatus().then(res => {
      setSystemStatus(res.data);
      setStatusLoading(false);
    }).catch(() => {
      setSystemStatus(null);
      setStatusLoading(false);
    });
  }, []);

  const updateConfig = async (key: keyof MockConfig, value: number) => {
    if (!mockConfig) return;
    setMockConfig({ ...mockConfig, [key]: value });
    try {
      await mockAPI.updateConfig({ [key]: value });
    } catch {
      message.error('配置更新失败');
    }
  };

  const handleSwitch = async () => {
    if (!targetVersion) {
      message.warning('请选择目标版本');
      return;
    }
    setSwitching(true);
    try {
      await modelAPI.switchModel(targetVersion);
      message.success(`模型热切换已启动: → ${targetVersion}`);
      setTimeout(fetchModelStatus, 3000);
    } catch (err: any) {
      message.error(err.message || '切换失败');
    } finally {
      setSwitching(false);
    }
  };

  const toggleMock = async (on: boolean) => {
    try {
      if (on) await mockAPI.start();
      else await mockAPI.stop();
      setMockRunning(on);
      message.success(on ? '模拟数据已启动' : '模拟数据已停止');
    } catch {
      message.error('操作失败');
    }
  };

  return (
    <div className="h-full flex flex-col gap-3 overflow-y-auto">
      <div className="panel-title">
        <SettingOutlined /> 系统设置
      </div>

      <Tabs
        defaultActiveKey="model"
        items={[
          {
            key: 'model',
            label: <span className="font-mono text-xs"><ExperimentOutlined /> AI 模型</span>,
            children: (
              <div className="space-y-4">
                <Card title="当前模型状态" size="small" className="bg-cyber-dark border-cyber-border">
                  <Descriptions column={2} size="small"
                    styles={{ label: { color: '#8899AA' }, content: { color: '#E8EDF2' } }}>
                    <Descriptions.Item label="活跃版本">
                      <Tag color="cyan">{modelStatus?.active_version || 'N/A'}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="就绪状态">
                      <Tag color={modelStatus?.is_ready ? 'green' : 'red'}>
                        {modelStatus?.is_ready ? '就绪' : '未就绪'}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="推理设备">{modelStatus?.device || '--'}</Descriptions.Item>
                  </Descriptions>
                </Card>

                <Card title="模型热切换" size="small" className="bg-cyber-dark border-cyber-border">
                  <Space>
                    <Select
                      placeholder="选择目标版本"
                      value={targetVersion || undefined}
                      onChange={setTargetVersion}
                      className="w-40"
                      options={Object.keys(modelStatus?.registry || {}).map(v => ({ value: v, label: v }))}
                    />
                    <Button
                      type="primary"
                      loading={switching}
                      onClick={handleSwitch}
                      icon={<ThunderboltOutlined />}
                    >
                      执行热切换
                    </Button>
                    <Button icon={<ReloadOutlined />} onClick={fetchModelStatus}>刷新</Button>
                  </Space>
                </Card>

                {modelStatus?.registry && Object.keys(modelStatus.registry).length > 0 && (
                  <Card title="版本仓库" size="small" className="bg-cyber-dark border-cyber-border">
                    <div className="space-y-2">
                      {Object.entries(modelStatus.registry).map(([v, info]) => (
                        <div key={v} className="flex items-center justify-between p-2 bg-cyber-black/50 rounded">
                          <div className="flex items-center gap-2">
                            <Tag color={info.status === 'active' ? 'green' : 'default'}>{v}</Tag>
                            <span className="text-text-secondary text-xs">{info.model_type}</span>
                          </div>
                          <div className="flex items-center gap-4 text-xs font-mono text-text-muted">
                            <span>{info.file_size_mb} MB</span>
                            <span>推理: {info.inference_count}次</span>
                            <span>平均: {info.avg_inference_ms}ms</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </Card>
                )}
              </div>
            ),
          },
          {
            key: 'system',
            label: <span className="font-mono text-xs"><DatabaseOutlined /> 系统</span>,
            children: (
              <div className="space-y-4">
                <Card title="模拟数据控制" size="small" className="bg-cyber-dark border-cyber-border">
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-text-secondary text-sm">模拟数据生成器</div>
                        <div className="text-text-muted text-xs">控制演示用的传感器和告警数据</div>
                      </div>
                      <Switch checked={mockRunning} onChange={toggleMock} />
                    </div>

                    {mockConfig && mockRunning && (
                      <>
                        <div>
                          <div className="text-text-secondary text-xs mb-1">
                            告警间隔: {mockConfig.alert_interval_seconds}s
                          </div>
                          <Slider
                            min={5} max={300} step={5}
                            value={mockConfig.alert_interval_seconds}
                            onChange={(v) => updateConfig('alert_interval_seconds', v)}
                            tooltip={{ formatter: (v) => `${v}s` }}
                          />
                        </div>
                        <div>
                          <div className="text-text-secondary text-xs mb-1">
                            每次告警数量: {mockConfig.alert_count_per_batch}
                          </div>
                          <Slider
                            min={1} max={10} step={1}
                            value={mockConfig.alert_count_per_batch}
                            onChange={(v) => updateConfig('alert_count_per_batch', v)}
                          />
                        </div>
                        <div>
                          <div className="text-text-secondary text-xs mb-1">
                            传感器间隔: {mockConfig.sensor_interval}s
                          </div>
                          <Slider
                            min={1} max={30} step={1}
                            value={mockConfig.sensor_interval}
                            onChange={(v) => updateConfig('sensor_interval', v)}
                            tooltip={{ formatter: (v) => `${v}s` }}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </Card>

                <Card title="连接状态" size="small" className="bg-cyber-dark border-cyber-border">
                  <Descriptions column={1} size="small"
                    styles={{ label: { color: '#8899AA' }, content: { color: '#E8EDF2' } }}>
                    <Descriptions.Item label="PostgreSQL">
                      {statusLoading ? <Tag>检测中</Tag> : (
                        <Tag color={systemStatus?.postgresql?.status === 'connected' ? 'green' : 'red'}>
                          {systemStatus?.postgresql?.status === 'connected' ? '已连接' : '连接失败'}
                        </Tag>
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="Redis">
                      {statusLoading ? <Tag>检测中</Tag> : (
                        <Tag color={systemStatus?.redis?.status === 'connected' ? 'green' : 'red'}>
                          {systemStatus?.redis?.status === 'connected' ? '已连接' : '连接失败'}
                        </Tag>
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="InfluxDB">
                      {statusLoading ? <Tag>检测中</Tag> : (
                        <Tag color={systemStatus?.influxdb?.status === 'connected' ? 'green' : systemStatus?.influxdb?.status === 'not_configured' ? 'default' : 'red'}>
                          {systemStatus?.influxdb?.status === 'connected' ? '已连接' : systemStatus?.influxdb?.status === 'not_configured' ? '未配置' : '连接失败'}
                        </Tag>
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="AI 模型">
                      {statusLoading ? <Tag>检测中</Tag> : (
                        <Tag color={systemStatus?.model?.status === 'ready' ? 'green' : 'orange'}>
                          {systemStatus?.model?.status === 'ready' ? `就绪 (${systemStatus?.model?.active_version})` : '未就绪'}
                        </Tag>
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label="SSE 实时流">
                      <Tag color="processing">活跃</Tag>
                    </Descriptions.Item>
                  </Descriptions>
                </Card>

                <Card title="关于系统" size="small" className="bg-cyber-dark border-cyber-border">
                  <Descriptions column={1} size="small"
                    styles={{ label: { color: '#8899AA' }, content: { color: '#E8EDF2' } }}>
                    <Descriptions.Item label="系统名称">智慧城市神经末梢</Descriptions.Item>
                    <Descriptions.Item label="版本号">v1.0.0</Descriptions.Item>
                    <Descriptions.Item label="前端">React 18 + TypeScript + Tailwind</Descriptions.Item>
                    <Descriptions.Item label="后端">FastAPI + YOLOv8</Descriptions.Item>
                    <Descriptions.Item label="AI 引擎">Ultralytics YOLO</Descriptions.Item>
                  </Descriptions>
                </Card>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
