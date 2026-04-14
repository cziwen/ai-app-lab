import { normalizeEnableLiveSubtitle } from './check-in';

describe('normalizeEnableLiveSubtitle', () => {
  it('accepts only boolean true as enabled', () => {
    expect(normalizeEnableLiveSubtitle(true)).toBe(true);
    expect(normalizeEnableLiveSubtitle(false)).toBe(false);
    expect(normalizeEnableLiveSubtitle(undefined)).toBe(false);
    expect(normalizeEnableLiveSubtitle(null)).toBe(false);
    expect(normalizeEnableLiveSubtitle(1)).toBe(false);
    expect(normalizeEnableLiveSubtitle('true')).toBe(false);
  });
});
