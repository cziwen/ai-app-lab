import type { LiveSubtitleBarProps } from '@/call-ui/types';

export const LiveSubtitleBar = ({ text }: LiveSubtitleBarProps) => {
  const hasText = Boolean(text?.trim());

  return (
    <div
      className={`subtitle-bar ${hasText ? '' : 'is-empty'}`.trim()}
      role={hasText ? 'status' : undefined}
      aria-live={hasText ? 'polite' : undefined}
      aria-hidden={!hasText}
    >
      {hasText ? text : ' '}
    </div>
  );
};
