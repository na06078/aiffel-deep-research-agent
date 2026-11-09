// 스파이크 연결 정본. 메인만 수정하며 서버·화면 작업자는 읽기 전용으로 사용한다.
export type RunStatus = 'running' | 'completed' | 'blocked' | 'failed' | 'cancelled' | 'interrupted';
export type ReleaseOutcome = 'success' | 'failure' | 'quality_fail';
// 기술 명세25절: 화면/제공자 mode와 다른 자료 선택. 레거시 기록 누락은local.
export type SourceMode = 'local' | 'web';
export interface WebUsage {
  search_attempts: number; search_limit?: number;
  fetch_attempts: number; fetch_limit?: number;
  observed_http_requests: number;
}
export interface BudgetSummary {
  attempt_count: number; max_attempts: number; remaining_attempts: number;
  spent_usd: number; reserved_usd: number; max_usd: number;
  input_tokens: number; output_tokens: number; unknown_count: number;
}
export interface Assignment { section_id: string; title: string; assignee: string }
export interface QualitySummary {
  passed: boolean | null; reasons: string[]; warnings?: string[];
  metrics?: Record<string, unknown>;
  semantic_review?: {
    통과: boolean;
    문제: { 유형: string; 설명: string; 본문발췌: string; 출처: string[] }[];
    검토요약: string;
  };
}
export interface RunFailure { code: string; message: string; actor: string }
export interface RunSnapshot {
  run_id: string;
  question: string;
  control_revision: number;
  status: RunStatus;
  phase: string;
  plan: Assignment[];
  report_id: string | null;
  // 서버가 이미 보내는 값을 타입에 반영한다(기술 명세27절). 계획 단계의 제목이 보고서 제출 전에도 들어 있으므로
  // 보고서 제목으로는 report_id가 있을 때만 쓴다. 이 필드가 없던 이전 기록은 undefined다.
  report_title?: string | null;
  draft: string | null;
  quality: QualitySummary;
  parent_report_id: string | null;
  created_at: string;
  updated_at: string;
  mock_requests: number;
  actual_model_requests: number;
  source_mode?: SourceMode;
  web_usage?: WebUsage;
  // 실패한 실행의 사유. 이 필드가 생기기 전의 실패 기록에는 없다.
  failure?: RunFailure | null;
}
export interface QueueItem { instruction_id: string; text: string; status: 'queued'; source_mode?: SourceMode; raw_text?: string }
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
  error: { code: string; message: string; status?: number | null; attempt_id?: string | null } | null;
  provider: 'test-double' | 'openai' | 'art-demo' | null;
}
export interface Snapshot {
  protocol_version: 1;
  mode: 'spike' | 'research';
  provider: 'test-double' | 'openai';
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
  actual_model_requests: number;
  budget?: BudgetSummary;
  selected_source_mode?: SourceMode;
  source_modes?: SourceMode[];
}
export interface Admission {
  instruction_id: string;
  run_id: string | null;
  mode: 'immediate' | 'queued';
  status: 'received' | 'queued' | 'applied';
  control_revision: number | null;
  kind?: 'research' | 'mode_change';
  source_mode?: SourceMode;
  state_seq?: number;
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
  source_mode?: SourceMode;
}
export interface ErrorEnvelope { error: { code: string; message: string } }

// 기술 명세29절: 조회만으로 만드는 안전한 보고서 표현. 정본/실행/변환 작업을 만들지 않는다.
export const REPORT_RENDERER_VERSION = 'markdown-it4-playwright1248-svg-v2';
export interface ReportPresentation {
  report_id: string;
  source_report_hash: string;
  renderer_version: string;
  html: string;
  warnings: string[];
  links: { text: string; url: string }[];
}

// 기술 명세23절: 변환은 연구/큐와 독립이며 동일 정본에 결합한다.
export type ExportFormat = 'html' | 'pdf';
export type ExportStatus = 'not_requested' | 'generating' | 'ready' | 'failed';
export interface ReportExport {
  report_id: string;
  source_report_hash: string;
  format: ExportFormat;
  renderer_version: string;
  status: ExportStatus;
  created_at: string | null;
  updated_at: string | null;
  sha256: string | null;
  download_url: string | null;
  error: { code: string; message: string } | null;
}
export interface ReportExports {
  report_id: string;
  source_report_hash: string;
  exports: ReportExport[];
}

// 기술 명세28절: 실행 삭제(즉시 영구 삭제). 서버와 화면이 같은 모양을 쓴다.
export interface RunDeleteCounts {
  events: number; calls: number; activity_spans: number; requests: number;
  reports: number; report_files: number; export_files: number;
  web_sources: number; web_reads: number;
}
export interface RunDeleteDependent { run_id: string; question: string; created_at: string }
export interface RunDeleteBlocker {
  code: 'run_running' | 'has_dependents';
  message: string;
  dependents?: RunDeleteDependent[];
}
export interface RunDeletePreview {
  run_id: string; deletable: boolean; blockers: RunDeleteBlocker[]; counts: RunDeleteCounts;
}
export interface RunDeleteResult {
  status: 'deleted'; run_id: string; counts: RunDeleteCounts; warnings: string[]; current_run_id: string | null;
}
