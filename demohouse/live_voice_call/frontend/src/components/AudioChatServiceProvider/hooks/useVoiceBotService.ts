// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// Licensed under the 【火山方舟】原型应用软件自用许可协议
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at 
//     https://www.volcengine.com/docs/82379/1433703
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License. 

import { useContext, useEffect, useRef, useState } from 'react';
import { AudioChatServiceContext } from '@/components/AudioChatServiceProvider/context';
import { Message } from '@arco-design/web-react';
import { useAudioChatState } from '@/components/AudioChatProvider/hooks/useAudioChatState';
import { useLogContent } from '@/components/AudioChatServiceProvider/hooks/useLogContent';
import { useAudioRecorder } from '@/components/AudioChatServiceProvider/hooks/useAudioRecorder';
import VoiceBotService, { type PlaybackSnapshot } from '@/utils/voice_bot_service';
import { EventType, type BotErrorPayload } from '@/types';
import { useSpeakerConfig } from '@/components/AudioChatServiceProvider/hooks/useSpeakerConfig';
import { useMessageList } from '@/components/AudioChatProvider/hooks/useMessageList';
import { useSyncRef } from '@/hooks/useSyncRef';
import { useWsUrl } from '@/components/AudioChatServiceProvider/hooks/useWsUrl';
import { useSessionAuth } from '@/auth/context';

const createReconnectClientId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `cid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
};
const PAGE_RECONNECT_CLIENT_ID = createReconnectClientId();

const appendTokenToWsUrl = (
  baseWsUrl: string,
  token?: string | null,
  reconnectClientId?: string | null,
) => {
  if (!token) {
    return baseWsUrl;
  }
  const trimmed = token.trim();
  if (!trimmed) {
    return baseWsUrl;
  }
  const normalizedClientId = reconnectClientId?.trim() ?? '';
  if (typeof window !== 'undefined') {
    const resolved = new URL(baseWsUrl, window.location.href);
    resolved.searchParams.set('token', trimmed);
    if (normalizedClientId) {
      resolved.searchParams.set('client_id', normalizedClientId);
    }
    return resolved.toString();
  }
  const separator = baseWsUrl.includes('?') ? '&' : '?';
  const base = `${baseWsUrl}${separator}token=${encodeURIComponent(trimmed)}`;
  if (!normalizedClientId) {
    return base;
  }
  return `${base}&client_id=${encodeURIComponent(normalizedClientId)}`;
};

const resolveReconnectMaxSeconds = () => {
  const fallback = 15;
  if (typeof process === 'undefined' || !process.env) {
    return fallback;
  }
  const raw =
    process.env.MODERN_PUBLIC_FRONTEND_RECONNECT_MAX_SECONDS ||
    process.env.FRONTEND_RECONNECT_MAX_SECONDS ||
    '15';
  const parsed = Number.parseInt(String(raw).trim(), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return parsed;
};

const FRONTEND_RECONNECT_MAX_SECONDS = resolveReconnectMaxSeconds();
const RECONNECT_DELAYS_MS = [500, 1000, 2000, 3000] as const;
const CLIENT_HANGUP_DRAIN_HOLD_MS = 120;
const CLIENT_HANGUP_DRAIN_MAX_WAIT_MS = 450;
const CLIENT_HANGUP_CLOSE_CODE = 4000;
const CLIENT_HANGUP_CLOSE_REASON = 'client_hangup';
const INTERVIEW_COMPLETED_CLOSE_CODE = 4001;
const INTERVIEW_COMPLETED_CLOSE_REASON = 'interview_completed';
const PLAYBACK_WATCHDOG_TIMEOUT_MS = 1500;
export const resolveRecorderStartDelayMs = () => {
  const fallback = 250;
  if (typeof process === 'undefined' || !process.env) {
    return fallback;
  }
  const raw =
    process.env.MODERN_PUBLIC_RECORDER_START_DELAY_MS ||
    process.env.RECORDER_START_DELAY_MS ||
    '250';
  const parsed = Number.parseInt(String(raw).trim(), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return fallback;
  }
  return parsed;
};
const RECORDER_START_DELAY_MS = resolveRecorderStartDelayMs();

export const shouldAbortReconnectFlow = (
  autoReconnectEnabled: boolean,
  manualDisconnect: boolean,
) => !autoReconnectEnabled || manualDisconnect;

export const shouldAcceptReconnectSuccess = (
  autoReconnectEnabled: boolean,
  manualDisconnect: boolean,
  onRejected: () => void,
) => {
  if (shouldAbortReconnectFlow(autoReconnectEnabled, manualDisconnect)) {
    onRejected();
    return false;
  }
  return true;
};

export const shouldClearPlaybackOnSocketClose = (
  autoReconnectEnabled: boolean,
  manualDisconnect: boolean,
) => !shouldAbortReconnectFlow(autoReconnectEnabled, manualDisconnect);

export const isInterviewCompletedSocketClose = (
  event?: Pick<CloseEvent, 'code' | 'reason'> | null,
) => {
  if (!event || event.code !== INTERVIEW_COMPLETED_CLOSE_CODE) {
    return false;
  }
  const reason = String(event.reason || '').trim();
  return !reason || reason === INTERVIEW_COMPLETED_CLOSE_REASON;
};

export const isStuckMediaPlayback = (
  snapshot?: PlaybackSnapshot | null,
) => {
  if (!snapshot) {
    return false;
  }
  return (
    snapshot.route === 'media-element' &&
    snapshot.playing &&
    snapshot.audioPaused === true &&
    snapshot.audioEnded === false
  );
};

export const shouldRunPlaybackWatchdog = (
  wsReady: boolean,
  ttsDone: boolean,
  playbackStopped: boolean,
  botTurnStarted: boolean,
) => wsReady && ttsDone && !playbackStopped && botTurnStarted;

export const useVoiceBotService = () => {
  const {
    wsReadyRef,
    setCurrentUserSentence,
    setCurrentBotSentence,
    serviceRef,
    configNeedUpdateRef,
  } = useContext(AudioChatServiceContext);
  const { recStart, recStop } = useAudioRecorder();
  const { currentSpeaker } = useSpeakerConfig();
  const currentSpeakerRef = useSyncRef(currentSpeaker);

  const { setChatMessages } = useMessageList();
  const {
    setWsConnected,
    setBotSpeaking,
    setBotAudioPlaying,
    setBotAudioLevel,
    setAudioUnlocked,
    setAudioRouteMode,
    setUserSpeaking,
  } = useAudioChatState();

  const { wsUrl } = useWsUrl();
  const { token } = useSessionAuth();
  const reconnectClientIdRef = useRef<string>(PAGE_RECONNECT_CLIENT_ID);
  const ttsDoneRef = useRef(false);
  const playbackStoppedRef = useRef(false);
  const botTurnStartedRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const recorderStartTimerRef = useRef<number | null>(null);
  const playbackWatchdogTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectStartAtRef = useRef(0);
  const autoReconnectEnabledRef = useRef(true);
  const manualDisconnectRef = useRef(false);
  const recoveringRef = useRef(false);
  const reconnectExhaustedRef = useRef(false);
  const [isRecovering, setIsRecovering] = useState(false);
  const [reconnectExhausted, setReconnectExhausted] = useState(false);

  const { log } = useLogContent();
  const clearReconnectTimer = () => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  };

  const clearPlaybackWatchdog = () => {
    if (playbackWatchdogTimerRef.current !== null) {
      window.clearTimeout(playbackWatchdogTimerRef.current);
      playbackWatchdogTimerRef.current = null;
    }
  };

  const clearRecorderStartTimer = () => {
    if (recorderStartTimerRef.current !== null) {
      window.clearTimeout(recorderStartTimerRef.current);
      recorderStartTimerRef.current = null;
    }
  };

  const stopRecovering = () => {
    recoveringRef.current = false;
    setIsRecovering(false);
  };

  const markReconnectExhausted = (value: boolean) => {
    reconnectExhaustedRef.current = value;
    setReconnectExhausted(value);
  };

  const connectToServer = async ({
    showError,
    connectLogLabel,
  }: {
    showError: boolean;
    connectLogLabel: string;
  }) => {
    if (!serviceRef.current) {
      return false;
    }
    const wsUrlWithToken = appendTokenToWsUrl(
      wsUrl,
      token,
      reconnectClientIdRef.current,
    );
    try {
      await serviceRef.current.connect(wsUrlWithToken);
      setWsConnected(true);
      log(`${connectLogLabel} success`);
      return true;
    } catch (_e) {
      log(`${connectLogLabel} failed`);
      if (showError) {
        Message.error('连接失败');
      }
      setWsConnected(false);
      return false;
    }
  };

  const scheduleReconnect = () => {
    if (!serviceRef.current) {
      return;
    }
    if (
      shouldAbortReconnectFlow(
        autoReconnectEnabledRef.current,
        manualDisconnectRef.current,
      )
    ) {
      stopRecovering();
      clearReconnectTimer();
      return;
    }

    const elapsedMs = Date.now() - reconnectStartAtRef.current;
    if (elapsedMs >= FRONTEND_RECONNECT_MAX_SECONDS * 1000) {
      log('reconnect timeout');
      autoReconnectEnabledRef.current = false;
      stopRecovering();
      clearReconnectTimer();
      markReconnectExhausted(true);
      return;
    }

    const nextDelay =
      RECONNECT_DELAYS_MS[
        Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS_MS.length - 1)
      ];
    clearReconnectTimer();
    reconnectTimerRef.current = window.setTimeout(async () => {
      if (
        !serviceRef.current ||
        shouldAbortReconnectFlow(
          autoReconnectEnabledRef.current,
          manualDisconnectRef.current,
        )
      ) {
        return;
      }
      const ok = await connectToServer({
        showError: false,
        connectLogLabel: 'reconnect',
      });
      if (ok) {
        const accepted = shouldAcceptReconnectSuccess(
          autoReconnectEnabledRef.current,
          manualDisconnectRef.current,
          () => {
            log('reconnect result ignored after manual stop, closing unexpected ws');
            serviceRef.current?.disconnectWsOnly();
            clearReconnectTimer();
            stopRecovering();
          },
        );
        if (!accepted) {
          return;
        }
        clearReconnectTimer();
        reconnectAttemptRef.current = 0;
        stopRecovering();
        manualDisconnectRef.current = false;
        autoReconnectEnabledRef.current = true;
        markReconnectExhausted(false);
        return;
      }
      reconnectAttemptRef.current += 1;
      scheduleReconnect();
    }, nextDelay);
  };
  const clearRecorderGate = () => {
    clearRecorderStartTimer();
    clearPlaybackWatchdog();
    ttsDoneRef.current = false;
    playbackStoppedRef.current = false;
    botTurnStartedRef.current = false;
  };

  const resetRecorderGateForBotTurn = () => {
    clearPlaybackWatchdog();
    ttsDoneRef.current = false;
    playbackStoppedRef.current = false;
    botTurnStartedRef.current = true;
  };

  const maybeStartRecorder = () => {
    if (!wsReadyRef.current) {
      return;
    }
    if (!ttsDoneRef.current || !playbackStoppedRef.current) {
      return;
    }
    if (recorderStartTimerRef.current !== null) {
      return;
    }
    recorderStartTimerRef.current = window.setTimeout(() => {
      recorderStartTimerRef.current = null;
      if (!wsReadyRef.current || !ttsDoneRef.current || !playbackStoppedRef.current) {
        return;
      }
      setCurrentUserSentence('');
      setCurrentBotSentence('');
      recStart();
      log('gate: recorder started');
      clearRecorderGate();
    }, RECORDER_START_DELAY_MS);
    log(`gate: recorder start delayed delay_ms=${RECORDER_START_DELAY_MS}`);
  };

  const formatPlaybackSnapshot = (snapshot: PlaybackSnapshot) =>
    `route=${snapshot.route} playing=${snapshot.playing ? '1' : '0'} queue=${snapshot.queueLength} paused=${snapshot.audioPaused === null ? 'na' : snapshot.audioPaused ? '1' : '0'} ended=${snapshot.audioEnded === null ? 'na' : snapshot.audioEnded ? '1' : '0'} ready=${snapshot.audioReadyState === null ? 'na' : String(snapshot.audioReadyState)} ctx=${snapshot.audioCtxState}`;

  const schedulePlaybackWatchdog = () => {
    clearPlaybackWatchdog();
    if (
      !shouldRunPlaybackWatchdog(
        wsReadyRef.current,
        ttsDoneRef.current,
        playbackStoppedRef.current,
        botTurnStartedRef.current,
      )
    ) {
      return;
    }
    playbackWatchdogTimerRef.current = window.setTimeout(() => {
      playbackWatchdogTimerRef.current = null;
      if (
        !shouldRunPlaybackWatchdog(
          wsReadyRef.current,
          ttsDoneRef.current,
          playbackStoppedRef.current,
          botTurnStartedRef.current,
        )
      ) {
        return;
      }
      const service = serviceRef.current;
      if (!service) {
        return;
      }
      const before = service.getPlaybackSnapshot();
      log(`gate: playback watchdog fired ${formatPlaybackSnapshot(before)}`);
      if (!isStuckMediaPlayback(before)) {
        return;
      }
      log('gate: playback watchdog detected stuck media, trying recovery');
      void (async () => {
        await service.handleForegroundResume('tts_watchdog');
        if (
          !shouldRunPlaybackWatchdog(
            wsReadyRef.current,
            ttsDoneRef.current,
            playbackStoppedRef.current,
            botTurnStartedRef.current,
          )
        ) {
          return;
        }
        const after = service.getPlaybackSnapshot();
        log(
          `gate: playback watchdog post-recovery ${formatPlaybackSnapshot(after)}`,
        );
        if (!isStuckMediaPlayback(after)) {
          return;
        }
        playbackStoppedRef.current = true;
        log('gate: playback watchdog forced playback stopped');
        maybeStartRecorder();
      })();
    }, PLAYBACK_WATCHDOG_TIMEOUT_MS);
  };

  const parseBotError = (payload?: Record<string, any> | BotErrorPayload) => {
    const error = (payload as BotErrorPayload | undefined)?.error;
    const message =
      typeof error?.message === 'string' && error.message.trim()
        ? error.message
        : '服务暂时不可用，请稍后重试';
    return {
      code: error?.code,
      message,
    };
  };

  const handleBotUpdateConfig = () => {
    if (!serviceRef.current) {
      return;
    }
    serviceRef.current.sendMessage({
      event: EventType.BotUpdateConfig,
      payload: {
        speaker: currentSpeakerRef.current,
      },
    });
    log(
      'send | event:' +
        EventType.BotUpdateConfig +
        ' payload: ' +
        JSON.stringify({
          speaker: currentSpeaker,
        }),
    );
  };

  const handleConnect = async () => {
    manualDisconnectRef.current = false;
    autoReconnectEnabledRef.current = true;
    markReconnectExhausted(false);
    reconnectAttemptRef.current = 0;
    reconnectStartAtRef.current = Date.now();
    clearReconnectTimer();
    stopRecovering();
    setTimeout(() => {
      if (!serviceRef.current) {
        return;
      }
      void connectToServer({
        showError: true,
        connectLogLabel: 'connect',
      });
    }, 0);
  };

  const resetWsState = () => {
    clearRecorderStartTimer();
    wsReadyRef.current = false;
    clearRecorderGate();
    setWsConnected(false);
    setUserSpeaking(false);
  };

  const resetMediaState = () => {
    clearRecorderStartTimer();
    clearRecorderGate();
    setBotSpeaking(false);
    setBotAudioPlaying(false);
    setBotAudioLevel(0);
    setAudioUnlocked(false);
    setAudioRouteMode('web-audio-fallback');
    setUserSpeaking(false);
    setCurrentUserSentence('');
    setCurrentBotSentence('');
  };

  const disconnectSession = () => {
    manualDisconnectRef.current = true;
    autoReconnectEnabledRef.current = false;
    markReconnectExhausted(false);
    clearReconnectTimer();
    clearRecorderStartTimer();
    clearPlaybackWatchdog();
    stopRecovering();
    wsReadyRef.current = false;
    serviceRef.current?.disconnectWsOnly();
    resetWsState();
  };

  const notifyClientHangup = async () => {
    manualDisconnectRef.current = true;
    autoReconnectEnabledRef.current = false;
    clearReconnectTimer();
    clearRecorderStartTimer();
    clearPlaybackWatchdog();
    stopRecovering();
    const sent =
      (await serviceRef.current?.sendMessageWithDrain(
        {
          event: EventType.ClientHangup,
          payload: { source: 'ui_hangup' },
        },
        {
          minHoldMs: CLIENT_HANGUP_DRAIN_HOLD_MS,
          maxWaitMs: CLIENT_HANGUP_DRAIN_MAX_WAIT_MS,
          pollIntervalMs: 20,
        },
      )) ?? false;
    log(
      'send | event:' +
        EventType.ClientHangup +
        ` drained=${sent ? '1' : '0'}`,
    );
    return sent;
  };

  const notifyClientEndAnswer = () => {
    serviceRef.current?.sendMessage({
      event: EventType.ClientEndAnswer,
      payload: { source: 'ui_end_answer' },
    });
    log('send | event:' + EventType.ClientEndAnswer);
  };

  const shutdownSession = () => {
    manualDisconnectRef.current = true;
    autoReconnectEnabledRef.current = false;
    markReconnectExhausted(false);
    clearReconnectTimer();
    clearRecorderStartTimer();
    clearPlaybackWatchdog();
    stopRecovering();
    wsReadyRef.current = false;
    serviceRef.current?.shutdown({
      wsCloseCode: CLIENT_HANGUP_CLOSE_CODE,
      wsCloseReason: CLIENT_HANGUP_CLOSE_REASON,
    });
    resetWsState();
    resetMediaState();
  };

  useEffect(() => {
    const service = new VoiceBotService({
      ws_url: wsUrl,
      onStartPlayAudio: data => {
        setBotAudioPlaying(true);
      },
      onAudioLevelChange: level => {
        setBotAudioLevel(level);
      },
      onAudioUnlockedChange: unlocked => {
        setAudioUnlocked(unlocked);
      },
      onAudioRouteModeChange: mode => {
        setAudioRouteMode(mode);
      },
      onLog: message => {
        log(message);
      },
      onStopPlayAudio: () => {
        clearPlaybackWatchdog();
        setBotAudioPlaying(false);
        setBotAudioLevel(0);
        if (!wsReadyRef.current) {
          return;
        }
        playbackStoppedRef.current = true;
        log('gate: playback stopped');
        maybeStartRecorder();
      },
      onClose: event => {
        clearPlaybackWatchdog();
        log(`ws closed code=${event.code} reason=${event.reason || '-'}`);
        resetWsState();
        if (isInterviewCompletedSocketClose(event)) {
          autoReconnectEnabledRef.current = false;
          manualDisconnectRef.current = true;
          markReconnectExhausted(false);
          stopRecovering();
          clearReconnectTimer();
          return;
        }
        const shouldReconnect = shouldClearPlaybackOnSocketClose(
          autoReconnectEnabledRef.current,
          manualDisconnectRef.current,
        );
        if (!shouldReconnect) {
          stopRecovering();
          clearReconnectTimer();
          return;
        }
        serviceRef.current?.clearPlaybackBuffer();
        if (!recoveringRef.current) {
          reconnectStartAtRef.current = Date.now();
          reconnectAttemptRef.current = 0;
          recoveringRef.current = true;
          setIsRecovering(true);
          markReconnectExhausted(false);
        }
        scheduleReconnect();
      },
      onError: event => {
        log('ws error');
        console.error(event);
      },
      handleJSONMessage: msg => {
        const { event, payload } = msg;
        log('receive | event:' + event + ' payload:' + JSON.stringify(payload));
        switch (event) {
          case EventType.BotReady:
            wsReadyRef.current = true;
            setChatMessages(prev => [...prev, { role: 'bot', content: '' }]);
            break;
          case EventType.SentencePartialRecognized: {
            const partial =
              (payload as { sentence?: string } | undefined)?.sentence || '';
            setCurrentUserSentence(partial);
            break;
          }
          case EventType.SentenceRecognized:
            recStop();
            const content =
              (payload as { sentence?: string } | undefined)?.sentence || '';
            setCurrentUserSentence(content);
            setChatMessages(prev => [
              ...prev,
              { role: 'user', content },
              { role: 'bot', content: '' },
            ]);
            break;
          case EventType.TTSSentenceStart:
            clearPlaybackWatchdog();
            if (!botTurnStartedRef.current) {
              resetRecorderGateForBotTurn();
            }
            const sentence =
              (payload as { sentence?: string } | undefined)?.sentence || '';
            setCurrentBotSentence(prevSentence => prevSentence + sentence);
            setChatMessages(prev => {
              const lastBotIndex = prev.findLastIndex(msg => msg.role === 'bot');
              if (lastBotIndex < 0) {
                return prev;
              }
              return prev.map((msg, idx) => {
                if (idx !== lastBotIndex) {
                  return msg;
                }
                return {
                  ...msg,
                  content: (msg.content || '') + sentence,
                };
              });
            });
            setBotSpeaking(true);
            break;
          case EventType.BotError: {
            const { code, message } = parseBotError(payload);
            log('receive | bot error payload:' + JSON.stringify(payload));
            if (code !== undefined) {
              log('receive | bot error code:' + String(code));
            }
            autoReconnectEnabledRef.current = false;
            manualDisconnectRef.current = true;
            clearReconnectTimer();
            stopRecovering();
            Message.error(message);
            resetWsState();
            resetMediaState();
            break;
          }
          case EventType.TTSDone:
            setBotSpeaking(false);
            ttsDoneRef.current = true;
            log('gate: tts done');
            maybeStartRecorder();
            schedulePlaybackWatchdog();
            if (configNeedUpdateRef.current) {
              handleBotUpdateConfig();
              configNeedUpdateRef.current = false;
            }
        }
      },
    });
    serviceRef.current = service;
    const tryUnlockAudio = () => {
      service.unlockAudio();
    };
    document.addEventListener('click', tryUnlockAudio, { passive: true });
    document.addEventListener('touchstart', tryUnlockAudio, { passive: true });
    document.addEventListener('keydown', tryUnlockAudio, { passive: true });
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void service.handleForegroundResume('visibilitychange');
      }
    };
    const onPageShow = () => {
      void service.handleForegroundResume('pageshow');
    };
    const onWindowFocus = () => {
      void service.handleForegroundResume('focus');
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('pageshow', onPageShow);
    window.addEventListener('focus', onWindowFocus);
    return () => {
      document.removeEventListener('click', tryUnlockAudio);
      document.removeEventListener('touchstart', tryUnlockAudio);
      document.removeEventListener('keydown', tryUnlockAudio);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('pageshow', onPageShow);
      window.removeEventListener('focus', onWindowFocus);
      clearReconnectTimer();
      clearRecorderStartTimer();
      clearPlaybackWatchdog();
      recoveringRef.current = false;
      reconnectExhaustedRef.current = false;
      service.shutdown();
      if (serviceRef.current === service) {
        serviceRef.current = null;
      }
    };
  }, [wsUrl, token]);

  return {
    handleConnect,
    disconnectSession,
    shutdownSession,
    notifyClientHangup,
    notifyClientEndAnswer,
    isRecovering,
    reconnectExhausted,
  };
};
