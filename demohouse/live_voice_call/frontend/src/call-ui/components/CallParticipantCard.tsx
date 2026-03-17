import type { CSSProperties } from 'react';
import type { CallParticipantCardProps } from '@/call-ui/types';

export const CallParticipantCard = ({
  participant,
  speaking = false,
  audioLevel = 0,
}: CallParticipantCardProps) => {
  return (
    <section
      className={`call-card ${speaking ? 'is-speaking' : ''}`}
      style={
        {
          '--voice-level': Math.max(0, Math.min(1, audioLevel)),
        } as CSSProperties
      }
    >
      <div className="avatar-shell">
        <div
          className="avatar-core"
          style={{ background: participant.color }}
          aria-hidden="true"
        />
      </div>
      <div className="card-footer">
        <strong>{participant.name}</strong>
        {speaking && <span className="speaking-dot">发言中...</span>}
      </div>
    </section>
  );
};
