import type { CallParticipantCardProps } from '@/call-ui/types';

export const CallParticipantCard = ({
  participant,
  speaking = false,
  showWave = false,
  waveClassName = 'wave',
}: CallParticipantCardProps) => {
  return (
    <section className={`call-card ${speaking ? 'is-speaking' : ''}`}>
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
      {showWave && <div className={waveClassName} />}
    </section>
  );
};
