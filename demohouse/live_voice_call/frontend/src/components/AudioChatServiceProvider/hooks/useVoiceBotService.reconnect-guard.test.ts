import {
  shouldAbortReconnectFlow,
  shouldAcceptReconnectSuccess,
  shouldClearPlaybackOnSocketClose,
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
