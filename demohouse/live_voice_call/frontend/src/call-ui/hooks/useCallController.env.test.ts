import {
  resolveLiveSubtitleEnabled,
  resolveManualEndAnswerEnabled,
} from './useCallController';

describe('resolveManualEndAnswerEnabled', () => {
  const originalEnv = process.env;

  afterEach(() => {
    process.env = originalEnv;
  });

  it('uses default false when env is missing', () => {
    process.env = { ...originalEnv };
    delete process.env.MODERN_PUBLIC_MANUAL_END_ANSWER_ENABLED;
    expect(resolveManualEndAnswerEnabled()).toBe(false);
  });

  it('parses true-like values', () => {
    process.env = {
      ...originalEnv,
      MODERN_PUBLIC_MANUAL_END_ANSWER_ENABLED: 'true',
    };
    expect(resolveManualEndAnswerEnabled()).toBe(true);
  });
});

describe('resolveLiveSubtitleEnabled', () => {
  const originalEnv = process.env;

  afterEach(() => {
    process.env = originalEnv;
  });

  it('uses default false when env is invalid', () => {
    process.env = {
      ...originalEnv,
      MODERN_PUBLIC_ENABLE_LIVE_SUBTITLE: 'invalid',
    };
    expect(resolveLiveSubtitleEnabled()).toBe(false);
  });

  it('parses true-like values', () => {
    process.env = {
      ...originalEnv,
      MODERN_PUBLIC_ENABLE_LIVE_SUBTITLE: '1',
    };
    expect(resolveLiveSubtitleEnabled()).toBe(true);
  });
});
