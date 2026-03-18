import type { EventType, IMessage } from '@/types';

export type CallMode = 'mock' | 'real';

export interface CallParticipant {
  id: string;
  name: string;
  role: 'interviewer' | 'user';
  color: string;
  muted?: boolean;
}

export interface TranscriptItem {
  id: string;
  role: 'bot' | 'user';
  content: string;
  createdAt: number;
}

export interface DebugPanelState {
  wsUrl: string;
  connected: boolean;
  logs: string[];
  currentUserSentence: string;
  currentBotSentence: string;
}

export interface CallUiState {
  mode: CallMode;
  isConnected: boolean;
  isInCall: boolean;
  interviewerSpeaking: boolean;
  candidateSpeaking: boolean;
  micOn: boolean;
  camOn: boolean;
  shareOn: boolean;
  elapsedSec: number;
  subtitle: string;
  endNotice?: string;
  interviewerAudioLevel?: number;
  userAudioLevel?: number;
  interviewer: CallParticipant;
  user: CallParticipant;
}

export type CallControlAction =
  | 'toggleMic'
  | 'toggleCam'
  | 'toggleShare'
  | 'hangUp'
  | 'connect'
  | 'toggleDebug'
  | 'toggleMessagePanel'
  | 'switchMode';

export interface CallController {
  uiState: CallUiState;
  debugState: DebugPanelState;
  transcripts: TranscriptItem[];
  debugAllowed: boolean;
  debugOpen: boolean;
  messagePanelOpen: boolean;
  setWsUrl: (nextUrl: string) => void;
  onControlAction: (action: CallControlAction) => void;
}

export interface MockCallScript {
  userSentence: string;
  botSentence: string;
}

export type WsEventEnvelope<TPayload = Record<string, unknown>> = {
  event: EventType;
  payload?: TPayload;
};

export interface WsSentencePayload {
  sentence?: string;
}

export interface WsReadyPayload {
  session?: string;
}

export type WsContractEventMap = {
  BotReady: WsEventEnvelope<WsReadyPayload>;
  SentenceRecognized: WsEventEnvelope<WsSentencePayload>;
  TTSSentenceStart: WsEventEnvelope<WsSentencePayload>;
  TTSDone: WsEventEnvelope<Record<string, never>>;
  BotError: WsEventEnvelope<{
    error?: { code?: string | number; message?: string };
  }>;
  BotUpdateConfig: WsEventEnvelope<{ speaker?: string }>;
  UserAudio: WsEventEnvelope<Record<string, never>>;
};

export interface TranscriptListProps {
  items: TranscriptItem[];
  emptyText?: string;
}

export interface LiveSubtitleBarProps {
  text: string;
}

export interface CallParticipantCardProps {
  participant: CallParticipant;
  speaking?: boolean;
  audioLevel?: number;
}

export interface CallControlBarProps {
  isInCall: boolean;
  debugAllowed: boolean;
  onAction: (action: CallControlAction) => void;
}

export interface DebugDrawerProps {
  open: boolean;
  mode: CallMode;
  state: DebugPanelState;
  transcripts: TranscriptItem[];
  messagePanelOpen: boolean;
  onToggleMode: () => void;
  onSetWsUrl: (nextUrl: string) => void;
  onConnect: () => void;
  onToggleMessages: () => void;
}

export const toTranscriptItems = (items: IMessage[]): TranscriptItem[] => {
  return items.map((item, index) => ({
    id: `${item.role}-${index}-${item.content.slice(0, 8)}`,
    role: item.role,
    content: item.content,
    createdAt: Date.now() + index,
  }));
};
