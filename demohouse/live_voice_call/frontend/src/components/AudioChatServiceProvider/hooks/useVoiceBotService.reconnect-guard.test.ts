import {
  isInterviewCompletedSocketClose,
  isStuckMediaPlayback,
  resolveRecorderStartDelayMs,
  shouldAbortReconnectFlow,
  shouldAcceptReconnectSuccess,
  shouldClearPlaybackOnSocketClose,
  shouldRunPlaybackWatchdog,
} from './useVoiceBotService';

describe('shouldAbortReconnectFlow', () => {
  it('returns false when reconnect is still allowed', () => {
    expect(shouldAbortReconnectFlow(true, false)).toBe(false);
  });

  it('returns true when reconnect is disabled explicitly', () => {
    expect(shouldAbortReconnectFlow(false, false)).toBe(true);
  });

  it('returns true when manual disconnect is active', () => {
    expect(shouldAbortReconnectFlow(true, true)).toBe(true);
  });
});

describe('shouldAcceptReconnectSuccess', () => {
  it('accepts reconnect success when intent still allows reconnect', () => {
    const onRejected = jest.fn();
    expect(shouldAcceptReconnectSuccess(true, false, onRejected)).toBe(true);
    expect(onRejected).not.toHaveBeenCalled();
  });

  it('rejects reconnect success and invokes rejection callback after manual stop', () => {
    const onRejected = jest.fn();
    expect(shouldAcceptReconnectSuccess(true, true, onRejected)).toBe(false);
    expect(onRejected).toHaveBeenCalledTimes(1);
  });
});

describe('shouldClearPlaybackOnSocketClose', () => {
  it('returns true when reconnect is still allowed', () => {
    expect(shouldClearPlaybackOnSocketClose(true, false)).toBe(true);
  });

  it('returns false when reconnect is disabled explicitly', () => {
    expect(shouldClearPlaybackOnSocketClose(false, false)).toBe(false);
  });

  it('returns false when manual disconnect is active', () => {
    expect(shouldClearPlaybackOnSocketClose(true, true)).toBe(false);
  });
});

describe('isInterviewCompletedSocketClose', () => {
  it('returns true when close code indicates normal interview completion', () => {
    expect(
      isInterviewCompletedSocketClose({ code: 4001, reason: 'interview_completed' }),
    ).toBe(true);
    expect(isInterviewCompletedSocketClose({ code: 4001, reason: '' })).toBe(true);
  });

  it('returns false when close code/reason does not match completion semantics', () => {
    expect(isInterviewCompletedSocketClose({ code: 1006, reason: '' })).toBe(false);
    expect(
      isInterviewCompletedSocketClose({ code: 4001, reason: 'unexpected_reason' }),
    ).toBe(false);
    expect(isInterviewCompletedSocketClose(undefined)).toBe(false);
  });
});

describe('isStuckMediaPlayback', () => {
  it('returns true for paused media-element playback that is still marked playing', () => {
    expect(
      isStuckMediaPlayback({
        route: 'media-element',
        playing: true,
        queueLength: 1,
        audioPaused: true,
        audioEnded: false,
        audioReadyState: 2,
        audioCtxState: 'running',
      }),
    ).toBe(true);
  });

  it('returns false for non-stuck snapshots', () => {
    expect(
      isStuckMediaPlayback({
        route: 'media-element',
        playing: true,
        queueLength: 1,
        audioPaused: false,
        audioEnded: false,
        audioReadyState: 4,
        audioCtxState: 'running',
      }),
    ).toBe(false);
    expect(
      isStuckMediaPlayback({
        route: 'web-audio-fallback',
        playing: true,
        queueLength: 0,
        audioPaused: null,
        audioEnded: null,
        audioReadyState: null,
        audioCtxState: 'running',
      }),
    ).toBe(false);
  });
});

describe('shouldRunPlaybackWatchdog', () => {
  it('returns true only when gate is waiting for playback stop after tts done', () => {
    expect(shouldRunPlaybackWatchdog(true, true, false, true)).toBe(true);
    expect(shouldRunPlaybackWatchdog(false, true, false, true)).toBe(false);
    expect(shouldRunPlaybackWatchdog(true, false, false, true)).toBe(false);
    expect(shouldRunPlaybackWatchdog(true, true, true, true)).toBe(false);
    expect(shouldRunPlaybackWatchdog(true, true, false, false)).toBe(false);
  });
});

describe('resolveRecorderStartDelayMs', () => {
  const originalEnv = process.env;
  afterEach(() => {
    process.env = originalEnv;
  });

  it('uses default when env is invalid', () => {
    process.env = {
      ...originalEnv,
      MODERN_PUBLIC_RECORDER_START_DELAY_MS: 'invalid',
    };
    expect(resolveRecorderStartDelayMs()).toBe(250);
  });

  it('uses configured delay when env is valid', () => {
    process.env = {
      ...originalEnv,
      MODERN_PUBLIC_RECORDER_START_DELAY_MS: '320',
    };
    expect(resolveRecorderStartDelayMs()).toBe(320);
  });
});
