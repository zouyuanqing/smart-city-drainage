import { Spin } from 'antd';
import { LoadingOutlined } from '@ant-design/icons';

interface LoadingProps {
  tip?: string;
  fullScreen?: boolean;
  size?: 'small' | 'default' | 'large';
}

export function Loading({ tip = '加载中...', fullScreen = false, size = 'large' }: LoadingProps) {
  const fontSize = size === 'small' ? 24 : size === 'default' ? 32 : 48;

  const content = (
    <div className="flex flex-col items-center justify-center gap-3">
      <Spin
        indicator={<LoadingOutlined style={{ fontSize, color: '#00D4FF' }} spin />}
      />
      {tip && <p className="text-text-muted text-sm font-mono">{tip}</p>}
    </div>
  );

  if (fullScreen) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-cyber-black z-50">
        {content}
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center py-12">
      {content}
    </div>
  );
}
