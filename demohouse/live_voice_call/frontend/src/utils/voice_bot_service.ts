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

export type AudioRouteMode = 'media-element' | 'web-audio-fallback';

interface IVoiceBotService {
  ws_url: string;
  handleJSONMessage: (json: JSONResponse) => void;
  onStartPlayAudio: (data: ArrayBuffer) => void;
  onStopPlayAudio: () => void;
  onAudioLevelChange?: (level: number) => void;
  onAudioUnlockedChange?: (unlocked: boolean) => void;
  onAudioRouteModeChange?: (mode: AudioRouteMode) => void;
  onLog?: (message: string) => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
}

const isLikelySafariMobile = () => {
  if (typeof navigator === 'undefined') {
    return false;
  }
  const ua = navigator.userAgent;
  const isIOS = /iPhone|iPad|iPod/i.test(ua);
  const isSafari = /Safari/i.test(ua) && !/Chrome|CriOS|EdgiOS|FxiOS/i.test(ua);
  return isIOS && isSafari;
};

const getAudioContext = () => {
  const audioContextCtor =
    window.AudioContext ||
    ((window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext as typeof AudioContext | undefined);
  if (!audioContextCtor) {
    throw new Error('AudioContext unavailable');
  }
  return new audioContextCtor();
};

export default class VoiceBotService {
  private ws_url: string;
  private ws?: WebSocket;
  private audioCtx: AudioContext;
  private source: AudioBufferSourceNode | undefined;
  private analyser: AnalyserNode | undefined;
  private analyserData: Uint8Array | undefined;
  private analyserFrameId: number | null = null;
  private audioChunks: ArrayBuffer[] = [];
  private mediaAudio: HTMLAudioElement | null = null;
  private mediaObjectUrl: string | null = null;
  private handleJSONMessage: (json: JSONResponse) => void;
  private onStartPlayAudio: (data: ArrayBuffer) => void;
  private onStopPlayAudio: () => void;
  private onAudioLevelChange?: (level: number) => void;
  private onAudioUnlockedChange?: (unlocked: boolean) => void;
  private onAudioRouteModeChange?: (mode: AudioRouteMode) => void;
  private onLog?: (message: string) => void;
  private onClose?: (event: CloseEvent) => void;
  private onErrorCallback?: (event: Event) => void;
  private audioRouteMode: AudioRouteMode;
  private audioUnlocked = false;
  protected playing = false;
  private disposed = false;

  constructor(props: IVoiceBotService) {
    this.ws_url = props.ws_url;
    this.audioCtx = getAudioContext();
    this.handleJSONMessage = props.handleJSONMessage;
    this.onStartPlayAudio = props.onStartPlayAudio;
    this.onStopPlayAudio = props.onStopPlayAudio;
    this.onAudioLevelChange = props.onAudioLevelChange;
    this.onAudioUnlockedChange = props.onAudioUnlockedChange;
    this.onAudioRouteModeChange = props.onAudioRouteModeChange;
    this.onLog = props.onLog;
    this.onClose = props.onClose;
    this.onErrorCallback = props.onError;
    this.audioRouteMode = isLikelySafariMobile()
      ? 'media-element'
      : 'web-audio-fallback';
    this.onAudioRouteModeChange?.(this.audioRouteMode);
    this.log(
      `audio init route=${this.audioRouteMode} audio_ctx_state=${this.audioCtx.state}`,
    );
  }

  public async connect(overrideWsUrl?: string): Promise<WebSocket> {
    const targetWsUrl = overrideWsUrl || this.ws_url;
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(targetWsUrl);
      ws.onopen = () => {
        if (this.audioCtx.state === 'closed') {
          this.audioCtx = getAudioContext();
          this.log('audio ctx recreated because previous context was closed');
        }
        this.disposed = false;
        this.ws = ws;
        this.log(
          `ws connected audio_ctx_state=${this.audioCtx.state} route=${this.audioRouteMode}`,
        );
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

  public async unlockAudio() {
    if (this.disposed) {
      return;
    }
    try {
      if (this.audioCtx.state === 'closed') {
        this.audioCtx = getAudioContext();
      }
      if (this.audioCtx.state !== 'running') {
        await this.audioCtx.resume();
      }
      const unlocked = this.audioCtx.state === 'running';
      if (unlocked !== this.audioUnlocked) {
        this.audioUnlocked = unlocked;
        this.onAudioUnlockedChange?.(unlocked);
      }
      this.log(
        `audio unlock attempted unlocked=${this.audioUnlocked} audio_ctx_state=${this.audioCtx.state}`,
      );
    } catch (error) {
      this.log(`audio unlock failed error=${String(error)}`);
    }
  }

  public sendMessage(message: WebRequest) {
    const data = pack(message);
    this.safeSend(data);
  }

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
      });
    } catch (error) {
      console.error(error);
      this.onErrorCallback?.(error as Event);
    }
  }

  private async handleAudioOnlyResponse(data: ArrayBuffer) {
    this.audioChunks.push(data);
    if (!this.playing) {
      this.onStartPlayAudio(data);
      this.playing = true;
      this.playNextAudioChunk();
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

    if (this.audioRouteMode === 'media-element') {
      await this.playChunkViaMediaElement(data);
      return;
    }
    await this.playChunkViaWebAudio(data);
  }

  private async playChunkViaMediaElement(data: ArrayBuffer) {
    if (!this.mediaAudio) {
      this.mediaAudio = new Audio();
      this.mediaAudio.preload = 'auto';
      (
        this.mediaAudio as HTMLAudioElement & { playsInline?: boolean }
      ).playsInline = true;
    }

    this.releaseMediaObjectUrl();
    this.mediaObjectUrl = URL.createObjectURL(
      new Blob([data], { type: 'audio/mpeg' }),
    );

    const currentAudio = this.mediaAudio;
    currentAudio.src = this.mediaObjectUrl;

    currentAudio.onended = () => {
      this.releaseMediaObjectUrl();
      this.playNextAudioChunk();
    };

    currentAudio.onerror = () => {
      this.releaseMediaObjectUrl();
      this.log('audio media-element playback failed, fallback to web-audio');
      this.setAudioRouteMode('web-audio-fallback');
      this.audioChunks.unshift(data);
      this.playNextAudioChunk();
    };

    try {
      await currentAudio.play();
      this.onAudioLevelChange?.(0.3);
      this.log('audio chunk played with media-element route');
    } catch (error) {
      this.releaseMediaObjectUrl();
      this.log(`audio media-element play() rejected, fallback error=${String(error)}`);
      this.setAudioRouteMode('web-audio-fallback');
      this.audioChunks.unshift(data);
      this.playNextAudioChunk();
    }
  }

  private async playChunkViaWebAudio(data: ArrayBuffer) {
    try {
      if (this.audioCtx.state !== 'running') {
        await this.audioCtx.resume();
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
      this.log('audio chunk played with web-audio route');
    } catch (error) {
      this.log(
        `audio web-audio decode/play failed error=${String(error)} ctx=${this.audioCtx.state}`,
      );
      this.playNextAudioChunk();
    }
  }

  private setAudioRouteMode(mode: AudioRouteMode) {
    if (this.audioRouteMode === mode) {
      return;
    }
    this.audioRouteMode = mode;
    this.onAudioRouteModeChange?.(mode);
    this.log(`audio route switched to ${mode}`);
  }

  private releaseMediaObjectUrl() {
    if (!this.mediaObjectUrl) {
      return;
    }
    URL.revokeObjectURL(this.mediaObjectUrl);
    this.mediaObjectUrl = null;
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
      } catch (_error) {}
      this.source.disconnect();
      this.source = undefined;
    }
    if (this.analyser) {
      this.analyser.disconnect();
      this.analyser = undefined;
    }
    this.analyserData = undefined;
    if (this.mediaAudio) {
      this.mediaAudio.pause();
      this.mediaAudio.src = '';
      this.mediaAudio.onended = null;
      this.mediaAudio.onerror = null;
      this.mediaAudio = null;
    }
    this.releaseMediaObjectUrl();
    if (this.audioCtx.state !== 'closed') {
      this.audioCtx.close();
    }
    this.audioUnlocked = false;
    this.onAudioUnlockedChange?.(false);
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

  private log(message: string) {
    this.onLog?.(`[AudioRuntime] ${message}`);
  }
}
