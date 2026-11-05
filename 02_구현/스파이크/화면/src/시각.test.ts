import { describe, expect, it } from 'vitest';
import { formatEventTime } from './상태';

// UTC 저장값과 명시적 +09:00 입력을 독립적인 한국 시간 기대값으로 대조한다.
describe('사건 표시 시각', () => {
  it('호스트 시간대와 무관하게 KST로 표시하며 날짜 경계에서 24시를 만들지 않는다', () => {
    expect(formatEventTime('2026-11-05T13:05:06Z')).toBe('22:05:06');
    expect(formatEventTime('2026-11-05T15:05:06Z')).toBe('00:05:06');
    expect(formatEventTime('2026-11-05T21:05:06+09:00')).toBe('21:05:06');
    expect(formatEventTime('invalid')).toBe('시각 확인불가');
  });
});
