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

import { type FC, type PropsWithChildren, useState } from 'react';
import { AudioChatContext } from '@/components/AudioChatProvider/context';
import type { IMessage } from '@/types';
import type { AudioRouteMode } from '@/utils/voice_bot_service';

export const AudioChatProvider: FC<PropsWithChildren> = ({ children }) => {
  const [wsConnected, setWsConnected] = useState(false);
  const [botSpeaking, setBotSpeaking] = useState(false);
  const [botAudioPlaying, setBotAudioPlaying] = useState(false);
  const [botAudioLevel, setBotAudioLevel] = useState(0);
  const [audioUnlocked, setAudioUnlocked] = useState(false);
  const [audioRouteMode, setAudioRouteMode] =
    useState<AudioRouteMode>('web-audio-fallback');
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [userAudioLevel, setUserAudioLevel] = useState(0);
  const [chatMessages, setChatMessages] = useState<IMessage[]>([]);
  return (
    <AudioChatContext.Provider
      value={{
        wsConnected,
        setWsConnected,
        botSpeaking,
        setBotSpeaking,
        botAudioPlaying,
        setBotAudioPlaying,
        botAudioLevel,
        setBotAudioLevel,
        audioUnlocked,
        setAudioUnlocked,
        audioRouteMode,
        setAudioRouteMode,
        userSpeaking,
        setUserSpeaking,
        userAudioLevel,
        setUserAudioLevel,
        chatMessages,
        setChatMessages,
      }}
    >
      {children}
    </AudioChatContext.Provider>
  );
};
