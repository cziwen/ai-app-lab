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
import VoiceBotService from '@/utils/voice_bot_service';
import { EventType, type BotErrorPayload } from '@/types';
import { useSpeakerConfig } from '@/components/AudioChatServiceProvider/hooks/useSpeakerConfig';
import { useMessageList } from '@/components/AudioChatProvider/hooks/useMessageList';
import { useSyncRef } from '@/hooks/useSyncRef';
import { useWsUrl } from '@/components/AudioChatServiceProvider/hooks/useWsUrl';
import { useSessionAuth } from '@/auth/context';

const appendTokenToWsUrl = (baseWsUrl: string, token?: string | null) => {
  if (!token) {
    return baseWsUrl;
  }
  const trimmed = token.trim();
  if (!trimmed) {
    return baseWsUrl;
  }
  if (typeof window !== 'undefined') {
    const resolved = new URL(baseWsUrl, window.location.href);
    resolved.searchParams.set('token', trimmed);
    return resolved.toString();
  }
  const separator = baseWsUrl.includes('?') ? '&' : '?';
  return `${baseWsUrl}${separator}token=${encodeURIComponent(trimmed)}`;
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
  const ttsDoneRef = useRef(false);
  const playbackStoppedRef = useRef(false);
  const botTurnStartedRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
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
    const wsUrlWithToken = appendTokenToWsUrl(wsUrl, token);
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
    ttsDoneRef.current = false;
    playbackStoppedRef.current = false;
    botTurnStartedRef.current = false;
  };

  const resetRecorderGateForBotTurn = () => {
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
    setCurrentUserSentence('');
    setCurrentBotSentence('');
    recStart();
    log('gate: recorder started');
    clearRecorderGate();
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
    wsReadyRef.current = false;
    clearRecorderGate();
    setWsConnected(false);
    setUserSpeaking(false);
  };

  const resetMediaState = () => {
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
    stopRecovering();
    wsReadyRef.current = false;
    serviceRef.current?.disconnectWsOnly();
    resetWsState();
  };

  const notifyClientHangup = async () => {
    manualDisconnectRef.current = true;
    autoReconnectEnabledRef.current = false;
    clearReconnectTimer();
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
        setBotAudioPlaying(false);
        setBotAudioLevel(0);
        if (!wsReadyRef.current) {
          return;
        }
        playbackStoppedRef.current = true;
        log('gate: playback stopped');
        maybeStartRecorder();
      },
      onClose: () => {
        log('ws closed');
        resetWsState();
        if (!autoReconnectEnabledRef.current || manualDisconnectRef.current) {
          stopRecovering();
          clearReconnectTimer();
          return;
        }
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
    return () => {
      document.removeEventListener('click', tryUnlockAudio);
      document.removeEventListener('touchstart', tryUnlockAudio);
      document.removeEventListener('keydown', tryUnlockAudio);
      clearReconnectTimer();
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
