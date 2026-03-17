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

import { decodeWebSocketResponse, pack } from '.';
import type { JSONResponse, WebRequest } from '@/types';
import { CONST } from '@/constant';

interface IVoiceBotService {
  ws_url: string;
  handleJSONMessage: (json: JSONResponse) => void;
  onStartPlayAudio: (data: ArrayBuffer) => void;
  onStopPlayAudio: () => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
}
export default class VoiceBotService {
  private ws_url: string;
  private ws?: WebSocket;
  // private sonic:any;
  private audioCtx: AudioContext;
  private source: AudioBufferSourceNode | undefined;
  private audioChunks: ArrayBuffer[] = [];
  private handleJSONMessage: (json: JSONResponse) => void;
  private onStartPlayAudio: (data: ArrayBuffer) => void;
  private onStopPlayAudio: () => void;
  private onClose?: (event: CloseEvent) => void;
  private onErrorCallback?: (event: Event) => void;
  protected playing = false;
  private disposed = false;
  constructor(props: IVoiceBotService) {
    this.ws_url = props.ws_url;
    this.audioCtx = new AudioContext();
    this.handleJSONMessage = props.handleJSONMessage;
    this.onStartPlayAudio = props.onStartPlayAudio;
    this.onStopPlayAudio = props.onStopPlayAudio;
    this.onClose = props.onClose;
    this.onErrorCallback = props.onError;
  }
  public async connect(): Promise<WebSocket> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.ws_url);
      ws.onopen = () => {
        if (this.audioCtx.state === 'closed') {
          this.audioCtx = new AudioContext();
        }
        this.disposed = false;
        this.ws = ws;
        resolve(ws);
      };
      ws.onerror = e => {
        reject(e);
        this.onErrorCallback?.(e);
      };
      ws.onmessage = e => this.onMessage(e);
      ws.onclose = e => {
        this.ws = undefined;
        this.onClose?.(e);
      };
    });
  }

  // 发送消息
  public sendMessage(message: WebRequest) {
    const data = pack(message);
    this.safeSend(data);
  }

  // 接收消息
  public onMessage(e: MessageEvent<any>) {
    try {
      e.data.arrayBuffer().then((buffer: ArrayBuffer) => {
        const resp = decodeWebSocketResponse(buffer);
        if (resp.messageType === CONST.SERVER_FULL_RESPONSE) {
          this.handleJSONMessage(resp.payload as JSONResponse);
        }
        if (resp.messageType === CONST.SERVER_AUDIO_ONLY_RESPONSE) {
          this.handleAudioOnlyResponse(resp.payload as ArrayBuffer);
        }
        // handleMessage?.(json);
      });
    } catch (e) {
      console.error(e);
      this.onErrorCallback?.(e as Event);
    }
  }
  private async handleAudioOnlyResponse(data: ArrayBuffer) {
    this.audioChunks.push(data);
    if (!this.playing) {
      this.onStartPlayAudio(data);
      this.playNextAudioChunk();
      this.playing = true;
    }
  }
  private async playNextAudioChunk() {
    if (this.disposed) {
      this.playing = false;
      return;
    }
    const data = this.audioChunks.shift();
    if (!data) {
      this.onStopPlayAudio();
      this.playing = false;
      return;
    }
    const audioBuffer = await this.audioCtx.decodeAudioData(
      new Uint8Array(data).buffer,
    );
    const source = this.audioCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.audioCtx.destination);
    source.addEventListener('ended', () => this.playNextAudioChunk());
    this.source = source;
    source.start(0);
  }
  private safeSend(data: Blob | ArrayBuffer | string) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('[VoiceBotService] skip send: websocket is not OPEN');
      return;
    }
    this.ws.send(data);
  }
  public stopAllMedia() {
    this.audioChunks = [];
    this.playing = false;
    this.onStopPlayAudio();
    if (this.source) {
      try {
        this.source.stop();
      } catch (_e) {}
      this.source.disconnect();
      this.source = undefined;
    }
    if (this.audioCtx.state !== 'closed') {
      this.audioCtx.close();
    }
  }
  public disconnectWsOnly() {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN ||
        this.ws.readyState === WebSocket.CONNECTING)
    ) {
      this.ws.close();
    }
    this.ws = undefined;
  }
  public shutdown() {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.disconnectWsOnly();
    this.stopAllMedia();
  }
}
