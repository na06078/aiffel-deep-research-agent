import type {EventDetail,Report} from './계약';
import {formatEventTime} from './상태';
export function Inspector({detail,loading}:{detail:EventDetail|null;loading:boolean}){
 return <aside className="inspector" data-testid="inspector" aria-label="선택한 사건 상세" aria-busy={loading}>
  <h2>사건 상세</h2>
  {loading?<p role="status">선택한 기록을 불러오는 중…</p>:detail?<>
   <p className="provider">{detail.provider==='art-demo'?'로컬 합성 장면':'서버 테스트 대역'}</p>
   <h3>{detail.summary}</h3>
   <dl>
    <dt>사건 ID</dt><dd>{detail.event_id}</dd>
    <dt>발생 시각 (KST)</dt>
    <dd>{formatEventTime(detail.occurred_at)}<br/><small>UTC 원본: {detail.occurred_at}</small></dd>
    <dt>역할 / 상태</dt><dd>{detail.actor} / {detail.status}</dd>
    <dt>실행 / 버전</dt><dd>{detail.run_id} / {detail.control_revision}</dd>
   </dl>
   <h3>입력</h3><pre>{JSON.stringify(detail.input,null,2)}</pre>
   <h3>출력</h3><pre>{JSON.stringify(detail.output,null,2)}</pre>
   {detail.error&&<p role="alert">{detail.error.code}: {detail.error.message}</p>}
  </>:<div className="empty"><span className="empty-mark" aria-hidden="true"/><h3>기록 하나를 골라 보세요</h3><p>말풍선이나 사건 행을 누르면 그 순간의 입력과 출력을 여기에 고정합니다.</p><p>새 사건이 도착해도 선택은 바뀌지 않습니다.</p></div>}
 </aside>;
}
export function ReportViewer({report,onClose}:{report:Report;onClose:()=>void}){
 return <section className="report-viewer" data-testid="report-viewer" aria-label="확정 보고서">
  <div className="section-heading"><h2>{report.title}</h2><button type="button" onClick={onClose}>문서 접기</button></div>
  <p className="muted">합성 검증 문서 · 실제 GPT 조사 결과가 아닙니다.</p>
  <dl className="report-meta"><dt>실행 / 문서</dt><dd>{report.run_id} / {report.report_id}</dd><dt>버전 / SHA256</dt><dd>{report.control_revision} / {report.sha256}</dd></dl>
  <pre className="markdown">{report.markdown}</pre>
  <a className="download" href={report.download_url} download>이 문서 Markdown 다운로드</a>
 </section>;
}
