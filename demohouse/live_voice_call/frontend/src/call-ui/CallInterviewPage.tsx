import { CallControlBar } from '@/call-ui/components/CallControlBar';
import { CallParticipantCard } from '@/call-ui/components/CallParticipantCard';
import { DebugDrawer } from '@/call-ui/components/DebugDrawer';
import { LiveSubtitleBar } from '@/call-ui/components/LiveSubtitleBar';
import {
  formatDuration,
  useCallController,
} from '@/call-ui/hooks/useCallController';

export const CallInterviewPage = () => {
  const controller = useCallController();
  const { uiState, debugState } = controller;

  return (
    <main className="call-page">
      <header className="call-header">
        <div>
          <h1> 焕贞医疗 面试通话</h1>
          <p>在线 AI 面试通话场景，请认真对待</p>
        </div>
        <div className="header-meta">
          <span
            className={`status-pill ${uiState.isConnected ? 'is-online' : 'is-offline'}`}
          >
            {uiState.isConnected ? '已连接' : '未连接'}
          </span>
          <span className="timer-pill">
            {formatDuration(uiState.elapsedSec)}
          </span>
        </div>
      </header>
      {uiState.endNotice && <div className="end-notice">{uiState.endNotice}</div>}

      <section className="call-grid">
        <CallParticipantCard
          participant={uiState.interviewer}
          speaking={uiState.interviewerSpeaking}
          audioLevel={uiState.interviewerAudioLevel}
        />
        <CallParticipantCard
          participant={uiState.user}
          speaking={uiState.candidateSpeaking}
          audioLevel={uiState.userAudioLevel}
        />
      </section>

      <LiveSubtitleBar text={uiState.subtitle} />
      <CallControlBar
        isInCall={uiState.isInCall}
        onAction={controller.onControlAction}
      />
      <DebugDrawer
        open={controller.debugOpen}
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
