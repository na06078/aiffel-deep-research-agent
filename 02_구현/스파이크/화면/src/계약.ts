// 스파이크 연결 정본. 메인만 수정하며 서버·화면 작업자는 읽기 전용으로 사용한다.
export type RunStatus = 'running' | 'completed' | 'blocked' | 'failed' | 'cancelled' | 'interrupted';
export type ReleaseOutcome = 'success' | 'failure' | 'quality_fail';
export interface Assignment { section_id: string; title: string; assignee: string }
export interface RunSnapshot {
  run_id: string;
  question: string;
  control_revision: number;
  status: RunStatus;
  phase: string;
  plan: Assignment[];
  report_id: string | null;
  draft: string | null;
  quality: { passed: boolean | null; reasons: string[] };
  parent_report_id: string | null;
  created_at: string;
  updated_at: string;
  mock_requests: number;
  actual_model_requests: 0;
}
export interface QueueItem { instruction_id: string; text: string; status: 'queued' }
export interface Gate {
  gate_id: string;
  run_id: string;
  control_revision: number;
  actor: string;
  section_id: string;
  created_at: string;
  is_stale: boolean;
}
export interface JournalEvent {
  event_id: string;
  run_id: string;
  control_revision: number;
  seq: number;
  occurred_at: string;
  type: string;
  actor: string;
  recipient: string | null;
  status: string;
  summary: string;
}
export interface EventDetail extends JournalEvent {
  input: unknown;
  output: unknown;
  error: { code: string; message: string } | null;
  provider: 'test-double' | 'art-demo' | null;
}
export interface Snapshot {
  protocol_version: 1;
  mode: 'spike';
  provider: 'test-double';
  current_run_id: string | null;
  runs: RunSnapshot[];
  queue: QueueItem[];
  queue_paused: boolean;
  queue_pause_reason: string | null;
  waiting: Gate[];
  events: JournalEvent[];
  events_has_more: boolean;
  high_watermark_seq: number;
  mock_requests: number;
  actual_model_requests: 0;
}
export interface Admission {
  instruction_id: string;
  run_id: string | null;
  mode: 'immediate' | 'queued';
  status: 'received' | 'queued' | 'applied';
  control_revision: number | null;
}
export interface Report {
  report_id: string;
  run_id: string;
  control_revision: number;
  title: string;
  markdown: string;
  sha256: string;
  created_at: string;
  download_url: string;
}
export interface ErrorEnvelope { error: { code: string; message: string } }
