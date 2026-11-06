import type { EventDetail, RunSnapshot } from './계약';

// 실행/시간/재생 연결 정본. 메인만 수정하며 작업자는 읽기 전용으로 사용한다.
export interface ActivitySpan {
  span_id: string;
  run_id: string;
  control_revision: number;
  actor: string;
  track_id: string;
  label: string;
  kind: 'node' | 'model_wait';
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'superseded' | 'interrupted';
  timing_source: 'monotonic' | 'recorded_events' | 'incomplete';
  event_ids: string[];
}

export interface RunArchive {
  protocol_version: 1;
  provider: 'test-double';
  run: RunSnapshot;
  revisions: RunSnapshot[];
  events: EventDetail[];
  spans: ActivitySpan[];
  high_watermark_seq: number;
  started_at: string;
  ended_at: string | null;
  recorded_at: string;
  duration_ms: number;
  has_measured_spans: boolean;
  truncated: false;
}

// GET /api/spike/runs/{run_id}/archive만 사용한다. 조회는 상태/호출/기록을 변경하지 않는다.
// UTC ISO 시각과 유한한0이상 밀리초를 사용한다. null은 미측정/미종료이지0초가 아니다.
// node 구간은 대기를 포함한 단계 전체 경과다. model_wait와 겹치므로 단순 합산하지 않는다.
// 과거 실행에 측정 구간이 없으면 사건 시점만 보여주며 세부 시간은 미측정으로 표시한다.
