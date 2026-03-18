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

export interface IMessage {
  role: 'bot' | 'user';
  content: string;
}

export type WebResponse = {
  event: string;
  payload?: Record<string, any>;
  data?: ArrayBuffer;
  reqid?: string;
  status_code: number;
  status_text: string;
};

export type WebRequest = {
  event: string;
  payload?: Record<string, any>;
  data?: Blob;
};

export enum EventType {
  BotReady = 'BotReady',
  SentenceRecognized = 'SentenceRecognized',
  TTSSentenceStart = 'TTSSentenceStart',
  TTSDone = 'TTSDone',
  BotError = 'BotError',
  BotUpdateConfig = 'BotUpdateConfig',
  UserAudio = 'UserAudio',
  QueueEntered = 'QueueEntered',
  QueueUpdate = 'QueueUpdate',
  QueueAdmitted = 'QueueAdmitted',
  QueueTimeout = 'QueueTimeout',
  QueueCancelled = 'QueueCancelled',
}
export interface IWebSocketResponse {
  messageType: number;
  payload: JSONResponse | ArrayBuffer;
}

export type BotErrorPayload = {
  error?: {
    code?: string | number;
    message?: string;
  };
};

export type JSONResponse = {
  event: EventType;
  payload?: Record<string, any> | BotErrorPayload;
};
