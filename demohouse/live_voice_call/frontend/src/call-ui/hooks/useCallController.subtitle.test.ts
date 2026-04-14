import { resolveInterviewLiveSubtitleEnabled } from './useCallController';

describe('resolveInterviewLiveSubtitleEnabled', () => {
  it('returns true only when interview setting is true', () => {
    expect(resolveInterviewLiveSubtitleEnabled(true)).toBe(true);
    expect(resolveInterviewLiveSubtitleEnabled(false)).toBe(false);
    expect(resolveInterviewLiveSubtitleEnabled(undefined)).toBe(false);
    expect(resolveInterviewLiveSubtitleEnabled(null)).toBe(false);
    expect(resolveInterviewLiveSubtitleEnabled('true')).toBe(false);
  });
});
