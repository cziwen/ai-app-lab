import { Message } from '@arco-design/web-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from '@modern-js/runtime/router';
import { useAudioChatState } from '@/components/AudioChatProvider/hooks/useAudioChatState';
import { useMessageList } from '@/components/AudioChatProvider/hooks/useMessageList';
import { useCurrentSentence } from '@/components/AudioChatServiceProvider/hooks/useCurrentSentence';
import { useAudioRecorder } from '@/components/AudioChatServiceProvider/hooks/useAudioRecorder';
import { useLogContent } from '@/components/AudioChatServiceProvider/hooks/useLogContent';
import { useVoiceBotService } from '@/components/AudioChatServiceProvider/hooks/useVoiceBotService';
import { useWsUrl } from '@/components/AudioChatServiceProvider/hooks/useWsUrl';
import { useSessionAuth } from '@/auth/context';
import type {
  CallController,
  CallControlAction,
  CallMode,
  MockCallScript,
  TranscriptItem,
} from '@/call-ui/types';
import { toTranscriptItems } from '@/call-ui/types';

const DEFAULT_SCRIPT: MockCallScript[] = [
  {
    userSentence: '你好，我准备好了，可以开始今天的面试吗？',
    botSentence: '当然可以。先做一个一分钟的自我介绍吧。',
  },
  {
    userSentence: '我擅长前端工程化，也参与过 AI 应用交互设计。',
    botSentence: '很好。你如何在实时语音场景里平衡延迟、可读性和可维护性？',
  },
];
const AUTO_REDIRECT_SECONDS = 15;
type EndPhase = 'idle' | 'waiting_last_audio' | 'countdown';

const formatDuration = (seconds: number) => {
  const mins = String(Math.floor(seconds / 60)).padStart(2, '0');
  const secs = String(seconds % 60).padStart(2, '0');
  return `${mins}:${secs}`;
};

const transcriptFromScript = (script: MockCallScript): TranscriptItem[] => {
  const now = Date.now();
  return [
    {
      id: `mock-user-${now}`,
      role: 'user',
      content: script.userSentence,
      createdAt: now,
    },
    {
      id: `mock-bot-${now + 1}`,
      role: 'bot',
      content: script.botSentence,
      createdAt: now + 1,
    },
  ];
};

export const useCallController = (): CallController => {
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<CallMode>('real');
  const [debugOpen, setDebugOpen] = useState(false);
  const [messagePanelOpen, setMessagePanelOpen] = useState(false);
  const [camOn, setCamOn] = useState(false);
  const [shareOn, setShareOn] = useState(false);
  const [mockConnected, setMockConnected] = useState(false);
  const [mockInCall, setMockInCall] = useState(false);
  const [mockMicOn, setMockMicOn] = useState(false);
  const [mockUserSentence, setMockUserSentence] = useState('');
  const [mockBotSentence, setMockBotSentence] = useState('');
  const [mockLogs, setMockLogs] = useState<string[]>([]);
  const [mockMessages, setMockMessages] = useState<TranscriptItem[]>([]);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [endPhase, setEndPhase] = useState<EndPhase>('idle');
  const [endCountdownSec, setEndCountdownSec] = useState<number | null>(null);

  const mockScriptIndexRef = useRef(0);
  const mockPlaybackTimerRef = useRef<number | null>(null);
  const endingRef = useRef(false);
  const prevWsConnectedRef = useRef(false);
  const connectingRef = useRef(false);

  const {
    wsConnected,
    botSpeaking,
    userSpeaking,
    botAudioPlaying,
    botAudioLevel,
    userAudioLevel,
    setUserSpeaking,
    setBotAudioLevel,
    setUserAudioLevel,
  } = useAudioChatState();
  const { chatMessages } = useMessageList();
  const { currentBotSentence, currentUserSentence } = useCurrentSentence();
  const { wsUrl, setWsUrl } = useWsUrl();
  const { recStart, recStop } = useAudioRecorder();
  const { handleConnect, disconnectSession, shutdownSession } =
    useVoiceBotService();
  const { logContent } = useLogContent();
  const { mediaStreamsRef } = useSessionAuth();

  const stopStream = useCallback((stream: MediaStream | null) => {
    if (!stream) {
      return;
    }
    for (const track of stream.getTracks()) {
      track.stop();
    }
  }, []);

  const releaseSessionMedia = useCallback(() => {
    stopStream(mediaStreamsRef.current.userMedia);
    stopStream(mediaStreamsRef.current.displayMedia);
    mediaStreamsRef.current.userMedia = null;
    mediaStreamsRef.current.displayMedia = null;
  }, [mediaStreamsRef, stopStream]);

  const cleanupCaptureResources = useCallback(() => {
    recStop();
    releaseSessionMedia();
    setBotAudioLevel(0);
    setUserAudioLevel(0);
    setCamOn(false);
    setShareOn(false);
    setElapsedSec(0);
  }, [recStop, releaseSessionMedia, setBotAudioLevel, setUserAudioLevel]);

  const startAutoFinishFlow = useCallback(() => {
    if (endingRef.current) {
      return;
    }
    endingRef.current = true;
    connectingRef.current = false;
    cleanupCaptureResources();
    disconnectSession();
    if (botAudioPlaying || botSpeaking) {
      setEndPhase('waiting_last_audio');
      setEndCountdownSec(null);
      return;
    }
    setEndPhase('countdown');
    setEndCountdownSec(AUTO_REDIRECT_SECONDS);
  }, [
    botAudioPlaying,
    botSpeaking,
    cleanupCaptureResources,
    disconnectSession,
  ]);

  const finishImmediately = useCallback(() => {
    endingRef.current = true;
    connectingRef.current = false;
    setEndPhase('idle');
    setEndCountdownSec(null);
    cleanupCaptureResources();
    shutdownSession();
    navigate(`/hangup-result${location.search}`);
  }, [cleanupCaptureResources, location.search, navigate, shutdownSession]);

  useEffect(() => {
    if (mode !== 'real' || endingRef.current) {
      return;
    }
    const timer = window.setInterval(() => {
      setElapsedSec(prev => prev + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [mode]);

  useEffect(() => {
    if (
      mode !== 'real' ||
      wsConnected ||
      endingRef.current ||
      connectingRef.current
    ) {
      return;
    }
    connectingRef.current = true;
    handleConnect();
    const guardTimer = window.setTimeout(() => {
      connectingRef.current = false;
    }, 1200);
    return () => window.clearTimeout(guardTimer);
  }, [handleConnect, mode, wsConnected]);

  useEffect(() => {
    if (mode !== 'mock' || !mockInCall) {
      return;
    }
    const script =
      DEFAULT_SCRIPT[mockScriptIndexRef.current % DEFAULT_SCRIPT.length];
    mockScriptIndexRef.current += 1;
    const log = (text: string) => {
      const entry = `[${new Date().toLocaleTimeString()}]\t${text}`;
      setMockLogs(prev => [...prev, entry]);
    };
    log('模拟 | 候选人发言');
    setMockUserSentence(script.userSentence);
    setMockBotSentence('');
    setUserSpeaking(true);
    window.setTimeout(() => {
      setUserSpeaking(false);
      log('模拟 | 识别完成');
      const scriptMessages = transcriptFromScript(script);
      setMockMessages(prev => [...prev, ...scriptMessages]);
      let index = 0;
      const fullText = script.botSentence;
      mockPlaybackTimerRef.current = window.setInterval(() => {
        index += 1;
        setMockBotSentence(fullText.slice(0, index));
        if (index >= fullText.length && mockPlaybackTimerRef.current) {
          window.clearInterval(mockPlaybackTimerRef.current);
          mockPlaybackTimerRef.current = null;
          log('模拟 | 面试官语音完成');
        }
      }, 30);
    }, 900);
  }, [mode, mockInCall, setUserSpeaking]);

  useEffect(() => {
    if (mode !== 'real') {
      prevWsConnectedRef.current = wsConnected;
      return;
    }
    const wasConnected = prevWsConnectedRef.current;
    if (wasConnected && !wsConnected && !endingRef.current) {
      startAutoFinishFlow();
    }
    prevWsConnectedRef.current = wsConnected;
  }, [mode, startAutoFinishFlow, wsConnected]);

  useEffect(() => {
    if (mode !== 'real' || endPhase !== 'waiting_last_audio') {
      return;
    }
    if (botAudioPlaying || botSpeaking) {
      return;
    }
    setEndPhase('countdown');
    setEndCountdownSec(AUTO_REDIRECT_SECONDS);
  }, [botAudioPlaying, botSpeaking, endPhase, mode]);

  useEffect(() => {
    if (endPhase !== 'countdown' || endCountdownSec === null) {
      return;
    }
    if (endCountdownSec <= 0) {
      navigate(`/hangup-result${location.search}`);
      return;
    }
    const timer = window.setTimeout(() => {
      setEndCountdownSec(prev => (prev === null ? null : prev - 1));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [endCountdownSec, endPhase, location.search, navigate]);

  const subtitle = useMemo(() => {
    if (mode === 'mock') {
      return mockBotSentence || mockUserSentence;
    }
    return currentBotSentence || currentUserSentence;
  }, [
    mode,
    mockBotSentence,
    mockUserSentence,
    currentBotSentence,
    currentUserSentence,
  ]);

  const endNotice = useMemo(() => {
    if (mode !== 'real') {
      return undefined;
    }
    if (endPhase === 'waiting_last_audio') {
      return '面试已结束，正在播放最后一句...';
    }
    if (endPhase === 'countdown' && endCountdownSec !== null) {
      return `面试已结束，${endCountdownSec} 秒后自动跳转结果页`;
    }
    return undefined;
  }, [endCountdownSec, endPhase, mode]);

  const realInCall = userSpeaking || botSpeaking || botAudioPlaying;
  const isConnected = mode === 'mock' ? mockConnected : wsConnected;
  const isInCall = mode === 'mock' ? mockInCall : realInCall;
  const interviewerSpeaking =
    mode === 'mock'
      ? mockBotSentence.length > 0
      : botSpeaking || botAudioPlaying;
  const candidateSpeaking =
    mode === 'mock'
      ? mockUserSentence.length > 0 && mockBotSentence.length === 0
      : userSpeaking;
  const micOn = mode === 'mock' ? mockMicOn : userSpeaking;
  const transcripts =
    mode === 'mock' ? mockMessages : toTranscriptItems(chatMessages);
  const logs = mode === 'mock' ? mockLogs : logContent;
  const debugState = {
    wsUrl,
    connected: isConnected,
    logs,
    currentUserSentence:
      mode === 'mock' ? mockUserSentence : currentUserSentence,
    currentBotSentence: mode === 'mock' ? mockBotSentence : currentBotSentence,
  };

  const onControlAction = (action: CallControlAction) => {
    switch (action) {
      case 'toggleMic':
        if (!isConnected) {
          Message.warning('请先连接');
          return;
        }
        if (mode === 'mock') {
          const next = !mockMicOn;
          setMockMicOn(next);
          setMockInCall(next);
          if (next) {
            setElapsedSec(0);
          } else {
            setMockUserSentence('');
            setMockBotSentence('');
          }
          return;
        }
        if (userSpeaking) {
          recStop();
        } else {
          recStart();
        }
        return;
      case 'toggleCam':
        setCamOn(prev => !prev);
        return;
      case 'toggleShare':
        setShareOn(prev => !prev);
        return;
      case 'hangUp':
        if (mode === 'mock') {
          setMockInCall(false);
          setMockMicOn(false);
          setMockUserSentence('');
          setMockBotSentence('');
          setUserSpeaking(false);
          setElapsedSec(0);
          if (mockPlaybackTimerRef.current) {
            window.clearInterval(mockPlaybackTimerRef.current);
            mockPlaybackTimerRef.current = null;
          }
          navigate(`/hangup-result${location.search}`);
          return;
        }
        finishImmediately();
        return;
      case 'connect':
        if (mode === 'mock') {
          setMockConnected(true);
          return;
        }
        handleConnect();
        return;
      case 'toggleDebug':
        setDebugOpen(prev => !prev);
        return;
      case 'toggleMessagePanel':
        setMessagePanelOpen(prev => !prev);
        return;
      case 'switchMode':
        setMode(prev => (prev === 'mock' ? 'real' : 'mock'));
        setElapsedSec(0);
        setBotAudioLevel(0);
        setUserAudioLevel(0);
        setEndPhase('idle');
        setEndCountdownSec(null);
        endingRef.current = false;
        connectingRef.current = false;
        return;
      default:
    }
  };

  return {
    uiState: {
      mode,
      isConnected,
      isInCall,
      interviewerSpeaking,
      candidateSpeaking,
      micOn,
      camOn,
      shareOn,
      elapsedSec,
      subtitle,
      endNotice,
      interviewerAudioLevel: botAudioLevel,
      userAudioLevel,
      interviewer: {
        id: 'interviewer',
        name: '面试官',
        role: 'interviewer',
        color: 'radial-gradient(circle at 30% 30%, #2f8fff, #0064e0)',
        muted: false,
      },
      user: {
        id: 'user',
        name: '候选人',
        role: 'user',
        color: 'radial-gradient(circle at 30% 30%, #25cab8, #11808d)',
        muted: !micOn,
      },
    },
    debugState,
    transcripts,
    debugOpen,
    messagePanelOpen,
    setWsUrl,
    onControlAction,
  };
};

export { formatDuration };
