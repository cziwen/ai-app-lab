import { resolveRecorderPrerollDropMs } from './useAudioRecorder';

describe('resolveRecorderPrerollDropMs', () => {
  const originalEnv = process.env;

  afterEach(() => {
    process.env = originalEnv;
  });

  it('uses default when env is missing', () => {
    process.env = { ...originalEnv };
    delete process.env.MODERN_PUBLIC_RECORDER_PREROLL_DROP_MS;
    delete process.env.RECORDER_PREROLL_DROP_MS;
    expect(resolveRecorderPrerollDropMs()).toBe(300);
  });

  it('uses configured value when env is valid', () => {
    process.env = {
      ...originalEnv,
      MODERN_PUBLIC_RECORDER_PREROLL_DROP_MS: '420',
    };
    expect(resolveRecorderPrerollDropMs()).toBe(420);
  });
});
