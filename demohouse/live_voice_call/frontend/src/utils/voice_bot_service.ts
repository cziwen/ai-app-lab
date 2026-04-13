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
export type PlaybackSnapshot = {
  route: AudioRouteMode;
  playing: boolean;
  queueLength: number;
  audioPaused: boolean | null;
  audioEnded: boolean | null;
  audioReadyState: number | null;
  audioCtxState: AudioContextState;
};

type WsCloseOptions = {
  code?: number;
  reason?: string;
};

type SendDrainOptions = {
  minHoldMs?: number;
  maxWaitMs?: number;
  pollIntervalMs?: number;
};

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
  private playbackEpoch = 0;
  protected playing = false;
  private disposed = false;
  private mediaRecoveryInFlight = false;

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

  public getPlaybackSnapshot(): PlaybackSnapshot {
    const audio = this.mediaAudio;
    return {
      route: this.audioRouteMode,
      playing: this.playing,
      queueLength: this.audioChunks.length,
      audioPaused: audio ? audio.paused : null,
      audioEnded: audio ? audio.ended : null,
      audioReadyState: audio ? audio.readyState : null,
      audioCtxState: this.audioCtx.state,
    };
  }

  public async handleForegroundResume(trigger: string) {
    if (this.disposed) {
      return;
    }
    await this.unlockAudio();
    const snapshot = this.getPlaybackSnapshot();
    this.logPlaybackSnapshot(
      `audio foreground resume trigger=${trigger}`,
      snapshot,
    );
    if (snapshot.route !== 'media-element') {
      return;
    }
    await this.tryResumeMediaPlayback(`foreground_${trigger}`, true);
  }

  public sendMessage(message: WebRequest): boolean {
    const data = pack(message);
    return this.safeSend(data);
  }

  public async sendMessageWithDrain(
    message: WebRequest,
    options?: SendDrainOptions,
  ): Promise<boolean> {
    const data = pack(message);
    const ws = this.ws;
    if (!ws || !this.safeSend(data, ws)) {
      return false;
    }

    const pollIntervalMs = Math.max(8, options?.pollIntervalMs ?? 20);
    const minHoldMs = Math.max(0, options?.minHoldMs ?? 0);
    const maxWaitMs = Math.max(minHoldMs, options?.maxWaitMs ?? 350);
    const startAt = Date.now();

    while (Date.now() - startAt < maxWaitMs) {
      const elapsed = Date.now() - startAt;
      if (ws.readyState !== WebSocket.OPEN) {
        break;
      }
      if (ws.bufferedAmount <= 0 && elapsed >= minHoldMs) {
        return true;
      }
      await new Promise<void>(resolve => {
        window.setTimeout(resolve, pollIntervalMs);
      });
    }

    return ws.readyState === WebSocket.OPEN && ws.bufferedAmount <= 0;
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
      void this.playNextAudioChunk(this.playbackEpoch);
    }
  }

  private async playNextAudioChunk(epoch: number) {
    if (this.disposed || epoch !== this.playbackEpoch) {
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
      await this.playChunkViaMediaElement(data, epoch);
      return;
    }
    await this.playChunkViaWebAudio(data, epoch);
  }

  private async playChunkViaMediaElement(data: ArrayBuffer, epoch: number) {
    if (this.disposed || epoch !== this.playbackEpoch) {
      return;
    }
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
      if (epoch !== this.playbackEpoch) {
        return;
      }
      this.releaseMediaObjectUrl();
      void this.playNextAudioChunk(epoch);
    };

    currentAudio.onerror = () => {
      if (epoch !== this.playbackEpoch) {
        return;
      }
      this.releaseMediaObjectUrl();
      this.log('audio media-element playback failed, fallback to web-audio');
      this.setAudioRouteMode('web-audio-fallback');
      this.audioChunks.unshift(data);
      void this.playNextAudioChunk(epoch);
    };

    currentAudio.onpause = () => {
      if (epoch !== this.playbackEpoch || this.disposed) {
        return;
      }
      void this.tryResumeMediaPlayback('media_onpause', true);
    };

    try {
      await currentAudio.play();
      if (this.disposed || epoch !== this.playbackEpoch) {
        currentAudio.pause();
        return;
      }
      this.onAudioLevelChange?.(0.3);
      this.log('audio chunk played with media-element route');
    } catch (error) {
      if (epoch !== this.playbackEpoch) {
        return;
      }
      this.releaseMediaObjectUrl();
      this.log(
        `audio media-element play() rejected, fallback error=${String(error)}`,
      );
      this.setAudioRouteMode('web-audio-fallback');
      this.audioChunks.unshift(data);
      void this.playNextAudioChunk(epoch);
    }
  }

  private async playChunkViaWebAudio(data: ArrayBuffer, epoch: number) {
    try {
      if (this.disposed || epoch !== this.playbackEpoch) {
        return;
      }
      if (this.audioCtx.state !== 'running') {
        await this.audioCtx.resume();
      }
      if (this.disposed || epoch !== this.playbackEpoch) {
        return;
      }
      const audioBuffer = await this.audioCtx.decodeAudioData(
        new Uint8Array(data).buffer,
      );
      if (this.disposed || epoch !== this.playbackEpoch) {
        return;
      }
      const source = this.audioCtx.createBufferSource();
      const analyser = this.audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      this.analyser = analyser;
      this.analyserData = new Uint8Array(analyser.fftSize);
      source.buffer = audioBuffer;
      source.connect(analyser);
      analyser.connect(this.audioCtx.destination);
      source.addEventListener('ended', () => {
        void this.playNextAudioChunk(epoch);
      });
      this.source = source;
      this.startAnalyserLoop();
      if (this.disposed || epoch !== this.playbackEpoch) {
        source.disconnect();
        this.source = undefined;
        return;
      }
      source.start(0);
      this.log('audio chunk played with web-audio route');
    } catch (error) {
      if (epoch !== this.playbackEpoch) {
        return;
      }
      this.log(
        `audio web-audio decode/play failed error=${String(error)} ctx=${this.audioCtx.state}`,
      );
      void this.playNextAudioChunk(epoch);
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

  private safeSend(
    data: Blob | ArrayBuffer | string,
    targetWs?: WebSocket,
  ): boolean {
    const ws = targetWs ?? this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn('[VoiceBotService] skip send: websocket is not OPEN');
      return false;
    }
    ws.send(data);
    return true;
  }

  public stopAllMedia() {
    this.clearPlaybackBuffer();
    this.mediaAudio = null;
    if (this.audioCtx.state !== 'closed') {
      this.audioCtx.close();
    }
    this.audioUnlocked = false;
    this.onAudioUnlockedChange?.(false);
  }

  public clearPlaybackBuffer() {
    this.playbackEpoch += 1;
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
      this.mediaAudio.onpause = null;
    }
    this.releaseMediaObjectUrl();
    this.log(
      `audio playback cleared media_audio_retained=${this.mediaAudio ? '1' : '0'}`,
    );
  }

  public disconnectWsOnly(options?: WsCloseOptions) {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN ||
        this.ws.readyState === WebSocket.CONNECTING)
    ) {
      this.ws.close(options?.code, options?.reason);
    }
    this.ws = undefined;
  }

  public shutdown(options?: { wsCloseCode?: number; wsCloseReason?: string }) {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.disconnectWsOnly({
      code: options?.wsCloseCode,
      reason: options?.wsCloseReason,
    });
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

  private isDocumentVisible() {
    if (typeof document === 'undefined') {
      return true;
    }
    if (typeof document.visibilityState === 'string') {
      return document.visibilityState === 'visible';
    }
    return !document.hidden;
  }

  private formatPlaybackSnapshot(snapshot: PlaybackSnapshot) {
    return (
      `route=${snapshot.route}` +
      ` playing=${snapshot.playing ? '1' : '0'}` +
      ` queue=${snapshot.queueLength}` +
      ` paused=${snapshot.audioPaused === null ? 'na' : snapshot.audioPaused ? '1' : '0'}` +
      ` ended=${snapshot.audioEnded === null ? 'na' : snapshot.audioEnded ? '1' : '0'}` +
      ` ready=${snapshot.audioReadyState === null ? 'na' : String(snapshot.audioReadyState)}` +
      ` ctx=${snapshot.audioCtxState}`
    );
  }

  private logPlaybackSnapshot(prefix: string, snapshot: PlaybackSnapshot) {
    this.log(`${prefix} ${this.formatPlaybackSnapshot(snapshot)}`);
  }

  private async tryResumeMediaPlayback(trigger: string, allowDegrade: boolean) {
    const audio = this.mediaAudio;
    if (!audio || this.audioRouteMode !== 'media-element') {
      return;
    }
    if (!this.isDocumentVisible()) {
      this.log(`audio resume skipped trigger=${trigger} reason=hidden`);
      return;
    }
    const before = this.getPlaybackSnapshot();
    this.logPlaybackSnapshot(`audio resume check trigger=${trigger}`, before);
    if (!audio.paused || audio.ended) {
      return;
    }
    if (this.mediaRecoveryInFlight) {
      this.log(`audio resume skipped trigger=${trigger} reason=in_flight`);
      return;
    }
    this.mediaRecoveryInFlight = true;
    try {
      await audio.play();
      this.logPlaybackSnapshot(
        `audio resume success trigger=${trigger}`,
        this.getPlaybackSnapshot(),
      );
    } catch (error) {
      this.log(
        `audio resume failed trigger=${trigger} error=${String(error)} allow_degrade=${allowDegrade ? '1' : '0'}`,
      );
      if (allowDegrade) {
        this.clearPlaybackBuffer();
        this.logPlaybackSnapshot(
          `audio resume degraded trigger=${trigger}`,
          this.getPlaybackSnapshot(),
        );
      }
    } finally {
      this.mediaRecoveryInFlight = false;
    }
  }
}
