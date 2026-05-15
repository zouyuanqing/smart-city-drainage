import { useState, useEffect } from 'react';
import { Button, Tag, Tabs, Tooltip, message, Modal, Input, Empty } from 'antd';
import {
  PlayCircleOutlined,
  StopOutlined,
  FullscreenOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { VideoPlayer } from '@components/video/VideoPlayer';
import { Loading } from '@components/common';
import { streamAPI } from '@/services/api';

interface ActiveStream {
  id: string;
  name: string;
  hlsUrl: string;
  status: string;
  [key: string]: any;
}

export function VideoMonitor() {
  const [streams, setStreams] = useState<ActiveStream[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('hls');
  const [startModalVisible, setStartModalVisible] = useState(false);
  const [startCameraId, setStartCameraId] = useState('');
  const [startRtspUrl, setStartRtspUrl] = useState('');
  const [starting, setStarting] = useState(false);

  const fetchStatus = async () => {
    try {
      const res = await streamAPI.getStatus();
      const activeStreams = Object.entries(res.data.streams || {})
        .filter(([_, info]: [string, any]) => info.is_active)
        .map(([id, info]: [string, any]) => ({
          id,
          name: info.name || id,
          hlsUrl: `/hls/${id}/index.m3u8`,
          status: info.is_healthy ? 'healthy' : 'unhealthy',
          ...info,
        }));
      setStreams(activeStreams);
    } catch {
      message.error('获取视频流状态失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, 10000);
    return () => clearInterval(timer);
  }, []);

  const handleStartStream = async () => {
    if (!startCameraId || !startRtspUrl) {
      message.warning('请填写完整信息');
      return;
    }
    setStarting(true);
    try {
      await streamAPI.start({ camera_id: startCameraId, rtsp_url: startRtspUrl });
      message.success('视频流启动成功');
      setStartModalVisible(false);
      setStartCameraId('');
      setStartRtspUrl('');
      fetchStatus();
    } catch {
      message.error('启动视频流失败');
    } finally {
      setStarting(false);
    }
  };

  if (loading) return <Loading tip="加载视频流..." />;

  return (
    <div className="h-full flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="panel-title">📹 视频监控矩阵</div>
        <div className="flex gap-2">
          <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => setStartModalVisible(true)}>
            启动视频流
          </Button>
          <Tooltip title="刷新">
            <Button size="small" icon={<ReloadOutlined />} onClick={fetchStatus} />
          </Tooltip>
        </div>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'hls',
            label: <span className="font-mono text-xs">HLS 流</span>,
            children: (
              <div className="grid grid-cols-2 gap-4">
                {streams.length > 0 ? (
                  streams.map((s) => (
                    <div key={s.id} className="video-container aspect-video">
                      <VideoPlayer streamType="hls" streamUrl={s.hlsUrl} muted />
                      <div className="absolute top-2 left-2 z-10">
                        <Tag color={s.status === 'healthy' ? 'green' : 'red'} className="font-mono text-[10px]">
                          {s.name}
                        </Tag>
                      </div>
                      <div className="absolute bottom-2 right-2 z-10 flex gap-1">
                        <Button
                          size="small"
                          danger
                          icon={<StopOutlined />}
                          onClick={() => streamAPI.stop(s.id).then(fetchStatus)}
                          className="font-mono text-[10px]"
                        >
                          停止
                        </Button>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="col-span-2">
                    <Empty description="暂无活跃视频流" />
                  </div>
                )}
              </div>
            ),
          },
          {
            key: 'local',
            label: <span className="font-mono text-xs">本地摄像头</span>,
            children: (
              <div className="grid grid-cols-2 gap-4">
                <div className="video-container aspect-video">
                  <VideoPlayer streamType="local" />
                </div>
              </div>
            ),
          },
        ]}
      />

      <Modal
        title="启动视频流"
        open={startModalVisible}
        onOk={handleStartStream}
        onCancel={() => setStartModalVisible(false)}
        confirmLoading={starting}
        okText="启动"
        cancelText="取消"
      >
        <div className="flex flex-col gap-3">
          <div>
            <div className="mb-1 text-sm">摄像头 ID</div>
            <Input
              value={startCameraId}
              onChange={(e) => setStartCameraId(e.target.value)}
              placeholder="例如: cam-001"
            />
          </div>
          <div>
            <div className="mb-1 text-sm">RTSP 地址</div>
            <Input
              value={startRtspUrl}
              onChange={(e) => setStartRtspUrl(e.target.value)}
              placeholder="例如: rtsp://admin:password@192.168.1.100:554/stream"
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
