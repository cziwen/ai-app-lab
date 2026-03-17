import type { LiveSubtitleBarProps } from '@/call-ui/types';

export const LiveSubtitleBar = ({ text }: LiveSubtitleBarProps) => {
  return (
    <div className="subtitle-bar" role="status" aria-live="polite">
      {text || '字幕将在通话开始后显示...'}
    </div>
  );
};
