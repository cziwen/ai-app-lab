import { useEffect, useRef } from 'react';
import type { CallParticipantCardProps } from '@/call-ui/types';

export const CallParticipantCard = ({
  participant,
  speaking = false,
  audioLevel = 0,
  variant = 'main',
  mirrored = false,
  videoStream = null,
  onClick,
}: CallParticipantCardProps) => {
  void audioLevel;
  const clickable = typeof onClick === 'function';
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hasVideo = Boolean(videoStream);

  useEffect(() => {
    if (!videoRef.current) {
      return;
    }
    videoRef.current.srcObject = videoStream;
    return () => {
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
    };
  }, [videoStream]);

  return (
    <section
      className={`call-card call-card-${variant} ${speaking ? 'is-speaking' : ''} ${
        mirrored ? 'is-mirrored' : ''
      } ${clickable ? 'is-clickable' : ''}`}
      onClick={onClick}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={
        clickable
          ? event => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onClick();
              }
            }
          : undefined
      }
    >
      {hasVideo ? (
        <video
          ref={videoRef}
          className="participant-video"
          autoPlay
          playsInline
          muted
        />
      ) : (
        <div className="avatar-shell">
          <div
            className="avatar-core"
            style={{ background: participant.color }}
            aria-hidden="true"
          />
        </div>
      )}
      <div className="card-footer">
        <strong>{participant.name}</strong>
      </div>
    </section>
  );
};
