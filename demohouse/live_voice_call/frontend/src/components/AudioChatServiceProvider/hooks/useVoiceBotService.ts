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
import { EventType } from '@/types';
import { useSpeakerConfig } from '@/components/AudioChatServiceProvider/hooks/useSpeakerConfig';
import { useMessageList } from '@/components/AudioChatProvider/hooks/useMessageList';
import { useSyncRef } from '@/hooks/useSyncRef';
import { useWsUrl } from '@/components/AudioChatServiceProvider/hooks/useWsUrl';

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
  const { setWsConnected, setBotSpeaking, setBotAudioPlaying } =
    useAudioChatState();

  const { wsUrl } = useWsUrl();

  const { log } = useLogContent();
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
      serviceRef.current
        .connect()
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

  useEffect(() => {
    serviceRef.current = new VoiceBotService({
      ws_url: wsUrl,
      onStartPlayAudio: data => {
        setBotAudioPlaying(true);
      },
      onStopPlayAudio: () => {
        setBotAudioPlaying(false);
        setCurrentUserSentence('');
        setCurrentBotSentence('');
        if (!wsReadyRef.current) {
          return;
        }
        recStart();
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
            const content = payload?.sentence || '';
            setCurrentUserSentence(content);
            setChatMessages(prev => [
              ...prev,
              { role: 'user', content },
              { role: 'bot', content: '' },
            ]);
            break;
          case EventType.TTSSentenceStart:
            setCurrentBotSentence(prevSentence => {
              const content = prevSentence + payload?.sentence || '';
              setChatMessages(prev => {
                const lastBotIndex = prev.findLastIndex(
                  msg => msg.role === 'bot',
                );
                const lastBotMsg = prev[lastBotIndex];

                const updatedBotMsg = {
                  ...lastBotMsg,
                  content: content,
                };
                return prev.map((msg, idx) => {
                  if (idx === lastBotIndex) {
                    return updatedBotMsg;
                  } else {
                    return msg;
                  }
                });
              });
              return content;
            });
            setBotSpeaking(true);
            break;
          case EventType.TTSDone:
            setBotSpeaking(false);
            if (configNeedUpdateRef.current) {
              handleBotUpdateConfig();
              configNeedUpdateRef.current = false;
            }
        }
      },
    });
  }, [wsUrl]);

  return {
    handleConnect,
  };
};
