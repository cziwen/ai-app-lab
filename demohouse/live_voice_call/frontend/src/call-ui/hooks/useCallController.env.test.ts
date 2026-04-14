import { resolveLiveSubtitleEnabled } from './useCallController';

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
