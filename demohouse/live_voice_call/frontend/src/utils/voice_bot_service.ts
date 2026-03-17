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
  onAudioLevelChange?: (level: number) => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
}
export default class VoiceBotService {
  private ws_url: string;
  private ws?: WebSocket;
  // private sonic:any;
  private audioCtx: AudioContext;
  private source: AudioBufferSourceNode | undefined;
  private analyser: AnalyserNode | undefined;
  private analyserData: Uint8Array | undefined;
  private analyserFrameId: number | null = null;
  private audioChunks: ArrayBuffer[] = [];
  private handleJSONMessage: (json: JSONResponse) => void;
  private onStartPlayAudio: (data: ArrayBuffer) => void;
  private onStopPlayAudio: () => void;
  private onAudioLevelChange?: (level: number) => void;
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
    this.onAudioLevelChange = props.onAudioLevelChange;
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
    const analyser = this.audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    this.analyser = analyser;
    this.analyserData = new Uint8Array(analyser.fftSize);
    source.buffer = audioBuffer;
    source.connect(analyser);
    analyser.connect(this.audioCtx.destination);
    source.addEventListener('ended', () => this.playNextAudioChunk());
    this.source = source;
    this.startAnalyserLoop();
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
    this.stopAnalyserLoop();
    this.onAudioLevelChange?.(0);
    this.onStopPlayAudio();
    if (this.source) {
      try {
        this.source.stop();
      } catch (_e) {}
      this.source.disconnect();
      this.source = undefined;
    }
    if (this.analyser) {
      this.analyser.disconnect();
      this.analyser = undefined;
    }
    this.analyserData = undefined;
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

  private startAnalyserLoop() {
    if (this.analyserFrameId !== null || !this.analyser || !this.analyserData) {
      return;
    }
    const tick = () => {
      if (!this.analyser || !this.analyserData) {
        this.analyserFrameId = null;
        return;
      }
      this.analyser.getByteTimeDomainData(this.analyserData);
      let sum = 0;
      for (let i = 0; i < this.analyserData.length; i += 1) {
        const v = (this.analyserData[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / this.analyserData.length);
      const normalizedLevel = Math.max(0, Math.min(1, (rms - 0.01) / 0.12));
      this.onAudioLevelChange?.(normalizedLevel);
      this.analyserFrameId = window.requestAnimationFrame(tick);
    };
    this.analyserFrameId = window.requestAnimationFrame(tick);
  }

  private stopAnalyserLoop() {
    if (this.analyserFrameId !== null) {
      window.cancelAnimationFrame(this.analyserFrameId);
      this.analyserFrameId = null;
    }
  }
}
