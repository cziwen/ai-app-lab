import type { PointerEvent as ReactPointerEvent } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CallControlBar } from '@/call-ui/components/CallControlBar';
import { CallParticipantCard } from '@/call-ui/components/CallParticipantCard';
import { DebugDrawer } from '@/call-ui/components/DebugDrawer';
import {
  formatDuration,
  useCallController,
} from '@/call-ui/hooks/useCallController';

type PipPosition = {
  x: number;
  y: number;
};

const PIP_EDGE_PADDING = 14;

const clamp = (value: number, min: number, max: number) => {
  return Math.min(Math.max(value, min), max);
};

const getDefaultPipPosition = (
  stageRect: DOMRect,
  pipRect: DOMRect,
): PipPosition => {
  const maxX = Math.max(
    PIP_EDGE_PADDING,
    stageRect.width - pipRect.width - PIP_EDGE_PADDING,
  );
  const maxY = Math.max(
    PIP_EDGE_PADDING,
    stageRect.height - pipRect.height - PIP_EDGE_PADDING,
  );

  return {
    x: maxX,
    y: Math.min(PIP_EDGE_PADDING, maxY),
  };
};

const clampPipPosition = (
  next: PipPosition,
  stageRect: DOMRect,
  pipRect: DOMRect,
): PipPosition => {
  const maxX = Math.max(
    PIP_EDGE_PADDING,
    stageRect.width - pipRect.width - PIP_EDGE_PADDING,
  );
  const maxY = Math.max(
    PIP_EDGE_PADDING,
    stageRect.height - pipRect.height - PIP_EDGE_PADDING,
  );

  return {
    x: clamp(next.x, PIP_EDGE_PADDING, maxX),
    y: clamp(next.y, PIP_EDGE_PADDING, maxY),
  };
};

const snapPipToHorizontalEdge = (
  next: PipPosition,
  stageRect: DOMRect,
  pipRect: DOMRect,
): PipPosition => {
  const clamped = clampPipPosition(next, stageRect, pipRect);
  const rightEdgeX = Math.max(
    PIP_EDGE_PADDING,
    stageRect.width - pipRect.width - PIP_EDGE_PADDING,
  );
  const distanceToLeft = Math.abs(clamped.x - PIP_EDGE_PADDING);
  const distanceToRight = Math.abs(rightEdgeX - clamped.x);

  return {
    x: distanceToLeft <= distanceToRight ? PIP_EDGE_PADDING : rightEdgeX,
    y: clamped.y,
  };
};

export const CallInterviewPage = () => {
  const controller = useCallController();
  const { uiState, debugState } = controller;
  const stageRef = useRef<HTMLElement | null>(null);
  const pipRef = useRef<HTMLDivElement | null>(null);
  const pipPositionRef = useRef<PipPosition | null>(null);
  const dragStateRef = useRef<{
    pointerId: number;
    offsetX: number;
    offsetY: number;
    startX: number;
    startY: number;
  } | null>(null);
  const dragMovedRef = useRef(false);

  const [primaryParticipantId, setPrimaryParticipantId] = useState<
    'interviewer' | 'user'
  >('interviewer');
  const [pipPosition, setPipPosition] = useState<PipPosition | null>(null);
  const [isDraggingPip, setIsDraggingPip] = useState(false);

  const mainParticipant =
    primaryParticipantId === 'interviewer' ? uiState.interviewer : uiState.user;
  const pipParticipant =
    primaryParticipantId === 'interviewer' ? uiState.user : uiState.interviewer;

  const mainSpeaking =
    primaryParticipantId === 'interviewer'
      ? uiState.interviewerSpeaking
      : uiState.candidateSpeaking;
  const pipSpeaking =
    primaryParticipantId === 'interviewer'
      ? uiState.candidateSpeaking
      : uiState.interviewerSpeaking;
  const mainAudioLevel =
    primaryParticipantId === 'interviewer'
      ? uiState.interviewerAudioLevel ?? 0
      : uiState.userAudioLevel ?? 0;
  const pipAudioLevel =
    primaryParticipantId === 'interviewer'
      ? uiState.userAudioLevel ?? 0
      : uiState.interviewerAudioLevel ?? 0;

  const syncPipPosition = useCallback(() => {
    if (!stageRef.current || !pipRef.current) {
      return;
    }
    const stageRect = stageRef.current.getBoundingClientRect();
    const pipRect = pipRef.current.getBoundingClientRect();
    setPipPosition(prev => {
      if (!prev) {
        return getDefaultPipPosition(stageRect, pipRect);
      }
      return clampPipPosition(prev, stageRect, pipRect);
    });
  }, []);

  useEffect(() => {
    pipPositionRef.current = pipPosition;
  }, [pipPosition]);

  useEffect(() => {
    syncPipPosition();
    window.addEventListener('resize', syncPipPosition);
    return () => {
      window.removeEventListener('resize', syncPipPosition);
    };
  }, [syncPipPosition]);

  const onSwapMainPip = useCallback(() => {
    if (dragMovedRef.current) {
      dragMovedRef.current = false;
      return;
    }
    setPrimaryParticipantId(prev =>
      prev === 'interviewer' ? 'user' : 'interviewer',
    );
  }, []);

  const onPipPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!stageRef.current || !pipRef.current) {
        return;
      }
      const pipRect = pipRef.current.getBoundingClientRect();
      dragStateRef.current = {
        pointerId: event.pointerId,
        offsetX: event.clientX - pipRect.left,
        offsetY: event.clientY - pipRect.top,
        startX: event.clientX,
        startY: event.clientY,
      };
      dragMovedRef.current = false;
      setIsDraggingPip(true);
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [],
  );

  const onPipPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!stageRef.current || !pipRef.current || !dragStateRef.current) {
        return;
      }
      if (dragStateRef.current.pointerId !== event.pointerId) {
        return;
      }

      const stageRect = stageRef.current.getBoundingClientRect();
      const pipRect = pipRef.current.getBoundingClientRect();
      const next = clampPipPosition(
        {
          x: event.clientX - stageRect.left - dragStateRef.current.offsetX,
          y: event.clientY - stageRect.top - dragStateRef.current.offsetY,
        },
        stageRect,
        pipRect,
      );

      setPipPosition(next);
      const deltaX = Math.abs(event.clientX - dragStateRef.current.startX);
      const deltaY = Math.abs(event.clientY - dragStateRef.current.startY);
      if (deltaX > 4 || deltaY > 4) {
        dragMovedRef.current = true;
      }
    },
    [],
  );

  const onPipPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (dragStateRef.current?.pointerId !== event.pointerId) {
        return;
      }

      const currentPosition = pipPositionRef.current;
      if (
        dragMovedRef.current &&
        stageRef.current &&
        pipRef.current &&
        currentPosition
      ) {
        const stageRect = stageRef.current.getBoundingClientRect();
        const pipRect = pipRef.current.getBoundingClientRect();
        const snapped = snapPipToHorizontalEdge(
          currentPosition,
          stageRect,
          pipRect,
        );
        setPipPosition(snapped);
      }

      dragStateRef.current = null;
      setIsDraggingPip(false);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    },
    [],
  );

  return (
    <main className="call-page call-page-facetime">
      <section className="call-stage" ref={stageRef}>
        <div className="overlay-top">
          <span
            className={`status-pill ${uiState.isConnected ? 'is-online' : 'is-offline'}`}
          >
            {uiState.isConnected ? '已连接' : '未连接'}
          </span>
          <span className="timer-pill">
            {formatDuration(uiState.elapsedSec)}
          </span>
        </div>

        {uiState.endNotice && (
          <div className="end-notice stage-end-notice">{uiState.endNotice}</div>
        )}

        <div className="main-video">
          <CallParticipantCard
            participant={mainParticipant}
            speaking={mainSpeaking}
            audioLevel={mainAudioLevel}
            variant="main"
            mirrored={mainParticipant.role === 'user'}
          />
        </div>

        <div
          className={`pip-window ${isDraggingPip ? 'is-dragging' : ''}`}
          ref={pipRef}
          style={
            pipPosition
              ? {
                  transform: `translate3d(${pipPosition.x}px, ${pipPosition.y}px, 0)`,
                }
              : undefined
          }
          onPointerDown={onPipPointerDown}
          onPointerMove={onPipPointerMove}
          onPointerUp={onPipPointerUp}
          onPointerCancel={onPipPointerUp}
        >
          <CallParticipantCard
            participant={pipParticipant}
            speaking={pipSpeaking}
            audioLevel={pipAudioLevel}
            variant="pip"
            mirrored={pipParticipant.role === 'user'}
            onClick={onSwapMainPip}
          />
        </div>

        <div className="overlay-bottom">
          <CallControlBar
            isInCall={uiState.isInCall}
            debugAllowed={controller.debugAllowed}
            onAction={controller.onControlAction}
          />
        </div>
      </section>

      <DebugDrawer
        open={controller.debugAllowed && controller.debugOpen}
        mode={uiState.mode}
        state={debugState}
        transcripts={controller.transcripts}
        messagePanelOpen={controller.messagePanelOpen}
        onToggleMode={() => controller.onControlAction('switchMode')}
        onSetWsUrl={controller.setWsUrl}
        onConnect={() => controller.onControlAction('connect')}
        onToggleMessages={() =>
          controller.onControlAction('toggleMessagePanel')
        }
      />
    </main>
  );
};
