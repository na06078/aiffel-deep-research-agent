import { describe, expect, it } from 'vitest';
import { currentRunEvents } from './상태';
import type { JournalEvent, RunSnapshot } from './계약';

// 독립 합성 사례: 가장 높은 seq라도 다른 실행/버전이면 현재 상태가 아니다.
describe('현재 역할의 실행·버전 소유', () => {
  it('이전 실행의 제출과 같은 실행의 옛 버전을 현재 역할에 섞지 않는다', () => {
    const events = [
      { event_id: 'old-publish', run_id: 'A', control_revision: 2, seq: 100, actor: 'publisher' },
      { event_id: 'current-plan', run_id: 'B', control_revision: 2, seq: 10, actor: 'planner' },
      { event_id: 'old-revision', run_id: 'B', control_revision: 1, seq: 99, actor: 'researcher-1' },
      { event_id: 'current-research', run_id: 'B', control_revision: 2, seq: 11, actor: 'researcher-1' },
    ] as JournalEvent[];
    const run = { run_id: 'B', control_revision: 2 } as RunSnapshot;
    expect(currentRunEvents(events, run).map(event => event.event_id))
      .toEqual(['current-plan', 'current-research']);
    expect(currentRunEvents(events, undefined)).toEqual([]);
    expect(events.map(event => event.event_id))
      .toEqual(['old-publish', 'current-plan', 'old-revision', 'current-research']);
  });
});
