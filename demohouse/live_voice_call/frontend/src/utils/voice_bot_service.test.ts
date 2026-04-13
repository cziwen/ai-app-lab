import VoiceBotService from './voice_bot_service';

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

const createDeferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(res => {
    resolve = res;
  });
  return { promise, resolve } as Deferred<T>;
};

describe('VoiceBotService playback clear guards', () => {
  const originalWindow = (globalThis as any).window;
  const originalNavigator = (globalThis as any).navigator;
  const originalUrl = (globalThis as any).URL;
  const originalAudio = (globalThis as any).Audio;

  const source = {
    buffer: null as AudioBuffer | null,
    start: jest.fn(),
    stop: jest.fn(),
    connect: jest.fn(),
    disconnect: jest.fn(),
    addEventListener: jest.fn(),
  };
  const analyser = {
    fftSize: 0,
    connect: jest.fn(),
    disconnect: jest.fn(),
    getByteTimeDomainData: jest.fn(),
  };

  class FakeAudioContext {
    state: AudioContextState = 'running';
    destination = {} as AudioNode;
    close = jest.fn(async () => {
      this.state = 'closed';
    });
    resume = jest.fn(async () => {
      this.state = 'running';
    });
    decodeAudioData = jest.fn(async () => ({}) as AudioBuffer);
    createBufferSource = jest.fn(
      () => source as unknown as AudioBufferSourceNode,
    );
    createAnalyser = jest.fn(() => analyser as unknown as AnalyserNode);
  }

  beforeEach(() => {
    jest.clearAllMocks();
    const g = globalThis as any;
    g.window = g;
    g.navigator = { userAgent: 'Mozilla/5.0 Chrome' };
    g.URL = {
      createObjectURL: jest.fn(() => 'blob:test'),
      revokeObjectURL: jest.fn(),
    };
    g.Audio = class {
      preload = '';
      src = '';
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onpause: (() => void) | null = null;
      paused = false;
      ended = false;
      readyState = 4;
      play = jest.fn(async () => {
        this.paused = false;
      });
      pause = jest.fn(() => {
        this.paused = true;
      });
    };
    g.AudioContext = FakeAudioContext;
    g.webkitAudioContext = undefined;
    g.requestAnimationFrame = jest.fn(() => 1);
    g.cancelAnimationFrame = jest.fn();
  });

  afterAll(() => {
    const g = globalThis as any;
    g.window = originalWindow;
    g.navigator = originalNavigator;
    g.URL = originalUrl;
    g.Audio = originalAudio;
  });

  const createService = () =>
    new VoiceBotService({
      ws_url: 'ws://localhost/ws',
      handleJSONMessage: () => {},
      onStartPlayAudio: () => {},
      onStopPlayAudio: jest.fn(),
      onAudioLevelChange: jest.fn(),
      onAudioUnlockedChange: jest.fn(),
      onAudioRouteModeChange: jest.fn(),
      onLog: jest.fn(),
    });

  const createSafariService = () => {
    (globalThis as any).navigator = {
      userAgent:
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1',
    };
    return createService();
  };

  it('clearPlaybackBuffer clears queue without closing AudioContext', () => {
    const service = createService();
    (service as any).audioChunks = [new ArrayBuffer(4), new ArrayBuffer(8)];
    (service as any).playing = true;
    (service as any).source = source;
    (service as any).analyser = analyser;
    (service as any).analyserData = new Uint8Array(8);
    const retainedMediaAudio = new (globalThis as any).Audio();
    (service as any).mediaAudio = retainedMediaAudio;

    const onStopPlayAudio = (service as any).onStopPlayAudio as jest.Mock;
    const onAudioLevelChange = (service as any).onAudioLevelChange as jest.Mock;
    const onLog = (service as any).onLog as jest.Mock;
    const ctx = (service as any).audioCtx as FakeAudioContext;

    service.clearPlaybackBuffer();

    expect((service as any).audioChunks).toHaveLength(0);
    expect((service as any).playing).toBe(false);
    expect(onStopPlayAudio).toHaveBeenCalledTimes(1);
    expect(onAudioLevelChange).toHaveBeenCalledWith(0);
    expect(source.stop).toHaveBeenCalledTimes(1);
    expect(source.disconnect).toHaveBeenCalledTimes(1);
    expect(analyser.disconnect).toHaveBeenCalledTimes(1);
    expect((service as any).mediaAudio).toBe(retainedMediaAudio);
    expect(retainedMediaAudio.pause).toHaveBeenCalledTimes(1);
    expect(retainedMediaAudio.src).toBe('');
    expect(ctx.close).not.toHaveBeenCalled();
    expect(onLog).toHaveBeenCalledWith(
      '[AudioRuntime] audio playback cleared media_audio_retained=1',
    );
  });

  it('drops stale decode result after clearPlaybackBuffer epoch bump', async () => {
    const service = createService();
    const ctx = (service as any).audioCtx as FakeAudioContext;
    const decodeDeferred = createDeferred<AudioBuffer>();
    ctx.decodeAudioData = jest.fn(() => decodeDeferred.promise);
    ctx.createBufferSource = jest.fn(
      () => source as unknown as AudioBufferSourceNode,
    );

    (service as any).handleAudioOnlyResponse(new ArrayBuffer(16));
    service.clearPlaybackBuffer();
    decodeDeferred.resolve({} as AudioBuffer);
    await Promise.resolve();
    await Promise.resolve();

    expect(ctx.createBufferSource).not.toHaveBeenCalled();
    expect(source.start).not.toHaveBeenCalled();
  });

  it('returns playback snapshot fields for media-element route', () => {
    const service = createSafariService();
    const mediaAudio = new (globalThis as any).Audio();
    mediaAudio.paused = true;
    mediaAudio.ended = false;
    mediaAudio.readyState = 2;
    (service as any).mediaAudio = mediaAudio;
    (service as any).playing = true;
    (service as any).audioChunks = [new ArrayBuffer(4)];

    expect(service.getPlaybackSnapshot()).toEqual({
      route: 'media-element',
      playing: true,
      queueLength: 1,
      audioPaused: true,
      audioEnded: false,
      audioReadyState: 2,
      audioCtxState: 'running',
    });
  });

  it('tries to resume when media element is paused unexpectedly', async () => {
    const service = createSafariService();
    const mediaAudio = new (globalThis as any).Audio();
    (service as any).mediaAudio = mediaAudio;
    (service as any).playing = true;
    (service as any).audioChunks = [new ArrayBuffer(4)];

    await (service as any).playChunkViaMediaElement(new ArrayBuffer(8), 0);
    mediaAudio.paused = true;
    mediaAudio.ended = false;
    mediaAudio.onpause?.();
    await Promise.resolve();

    expect(mediaAudio.play).toHaveBeenCalledTimes(2);
  });

  it('degrades to stop playback when foreground resume cannot recover', async () => {
    const service = createSafariService();
    const onStopPlayAudio = (service as any).onStopPlayAudio as jest.Mock;
    const mediaAudio = new (globalThis as any).Audio();
    mediaAudio.paused = true;
    mediaAudio.ended = false;
    mediaAudio.play = jest.fn(async () => {
      throw new Error('blocked');
    });
    (service as any).mediaAudio = mediaAudio;
    (service as any).playing = true;
    (service as any).audioChunks = [new ArrayBuffer(4), new ArrayBuffer(8)];

    await service.handleForegroundResume('test');

    expect(onStopPlayAudio).toHaveBeenCalledTimes(1);
    expect((service as any).audioChunks).toHaveLength(0);
    expect((service as any).playing).toBe(false);
  });
});
