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

import {
  createContext,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import type VoiceBotService from '@/utils/voice_bot_service';

type AudioChatServiceContextType = {
  wsUrl: string;
  setWsUrl: Dispatch<SetStateAction<string>>;
  //
  logContent: string[];
  setLogContent: Dispatch<SetStateAction<string[]>>;
  //
  serviceRef: MutableRefObject<VoiceBotService | null>;
  recorderRef: MutableRefObject<any>;
  wsReadyRef: MutableRefObject<boolean>;
  //
  sendPcmBufferRef: MutableRefObject<Int16Array>;
  sendChunkRef: MutableRefObject<any>;
  sendLastFrameRef: MutableRefObject<Int16Array | null>;
  //
  currentSpeaker: string;
  setCurrentSpeaker: Dispatch<string>;
  configNeedUpdateRef: MutableRefObject<boolean>;
  //
  currentUserSentence: string;
  setCurrentUserSentence: Dispatch<SetStateAction<string>>;
  currentBotSentence: string;
  setCurrentBotSentence: Dispatch<SetStateAction<string>>;
};

export const AudioChatServiceContext =
  createContext<AudioChatServiceContextType>({} as unknown as never);
