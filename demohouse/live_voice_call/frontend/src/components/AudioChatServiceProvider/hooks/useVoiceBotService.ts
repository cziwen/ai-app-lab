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

import { useContext, useEffect } from 'react';
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
    setUserSpeaking,
  } = useAudioChatState();

  const { wsUrl } = useWsUrl();
  const { token } = useSessionAuth();

  const { log } = useLogContent();
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
        EventType.UserAudio +
        ' payload: ' +
        JSON.stringify({
          speaker: currentSpeaker,
        }),
    );
  };

  const handleConnect = async () => {
    setTimeout(() => {
      if (!serviceRef.current) {
        return;
      }
      const wsUrlWithToken = appendTokenToWsUrl(wsUrl, token);
      serviceRef.current
        .connect(wsUrlWithToken)
        .then(() => {
          setWsConnected(true);
          log('connect success');
        })
        .catch(e => {
          log('connect failed');
          Message.error('连接失败');
          setWsConnected(false);
        });
    }, 0);
  };

  const resetWsState = () => {
    wsReadyRef.current = false;
    setWsConnected(false);
    setUserSpeaking(false);
  };

  const resetMediaState = () => {
    setBotSpeaking(false);
    setBotAudioPlaying(false);
    setBotAudioLevel(0);
    setUserSpeaking(false);
    setCurrentUserSentence('');
    setCurrentBotSentence('');
  };

  const disconnectSession = () => {
    wsReadyRef.current = false;
    serviceRef.current?.disconnectWsOnly();
    resetWsState();
  };

  const shutdownSession = () => {
    wsReadyRef.current = false;
    serviceRef.current?.shutdown();
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
      onStopPlayAudio: () => {
        setBotAudioPlaying(false);
        setBotAudioLevel(0);
        if (!wsReadyRef.current) {
          return;
        }
        setCurrentUserSentence('');
        setCurrentBotSentence('');
        recStart();
      },
      onClose: () => {
        log('ws closed');
        resetWsState();
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
            Message.error(message);
            resetWsState();
            resetMediaState();
            break;
          }
          case EventType.QueueEntered:
          case EventType.QueueUpdate: {
            const queuePayload = payload as
              | { position?: number; active?: number; limit?: number }
              | undefined;
            const position = queuePayload?.position;
            if (typeof position === 'number' && position > 0) {
              setCurrentBotSentence(`当前排队第 ${position} 位，请稍候`);
            }
            break;
          }
          case EventType.QueueAdmitted: {
            setCurrentBotSentence('排队结束，正在进入面试...');
            break;
          }
          case EventType.QueueTimeout: {
            Message.warning('排队超时，请稍后重试');
            resetWsState();
            resetMediaState();
            break;
          }
          case EventType.QueueCancelled: {
            Message.warning('排队已取消');
            resetWsState();
            resetMediaState();
            break;
          }
          case EventType.TTSDone:
            setBotSpeaking(false);
            if (configNeedUpdateRef.current) {
              handleBotUpdateConfig();
              configNeedUpdateRef.current = false;
            }
        }
      },
    });
    serviceRef.current = service;
    return () => {
      service.shutdown();
      if (serviceRef.current === service) {
        serviceRef.current = null;
      }
    };
  }, [wsUrl]);

  return {
    handleConnect,
    disconnectSession,
    shutdownSession,
  };
};
