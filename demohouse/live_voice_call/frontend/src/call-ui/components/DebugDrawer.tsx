import { useMemo, useState } from 'react';
import { TranscriptList } from '@/call-ui/components/TranscriptList';
import type { DebugDrawerProps } from '@/call-ui/types';

export const DebugDrawer = ({
  open,
  mode,
  state,
  transcripts,
  messagePanelOpen,
  onToggleMode,
  onSetWsUrl,
  onConnect,
  onToggleMessages,
}: DebugDrawerProps) => {
  const [draftWsUrl, setDraftWsUrl] = useState(state.wsUrl);

  const modeLabel = useMemo(() => {
    return mode === 'mock' ? '模拟模式' : '真实模式';
  }, [mode]);

  return (
    <aside className={`debug-drawer ${open ? 'is-open' : ''}`}>
      <div className="debug-header">
        <h2>调试面板</h2>
        <button type="button" className="link-btn" onClick={onToggleMode}>
          {modeLabel}
        </button>
      </div>
      <div className="ws-config-row">
        <input
          value={draftWsUrl}
          onChange={event => setDraftWsUrl(event.target.value)}
          className="text-input"
          aria-label="ws url"
        />
        <button
          type="button"
          className="link-btn"
          onClick={() => {
            onSetWsUrl(draftWsUrl);
            onConnect();
          }}
        >
          连接
        </button>
      </div>
      <div className="status-grid">
        <div>连接状态: {state.connected ? '已连接' : '未连接'}</div>
        <div>用户识别: {state.currentUserSentence || '-'}</div>
        <div>模型回复: {state.currentBotSentence || '-'}</div>
      </div>
      <div className="debug-actions">
        <button type="button" className="link-btn" onClick={onToggleMessages}>
          {messagePanelOpen ? '隐藏记录' : '展开记录'}
        </button>
      </div>
      {messagePanelOpen && (
        <div className="transcript-panel">
          <TranscriptList items={transcripts} />
        </div>
      )}
      <textarea
        readOnly
        className="log-box"
        value={state.logs.slice().reverse().join('\n')}
      />
    </aside>
  );
};
