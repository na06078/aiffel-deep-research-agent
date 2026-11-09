import type {EventDetail,Report,QualitySummary} from './계약';
import {formatEventTime} from './상태';
import {ReportPresentationView} from './보고서표현';
import {ReportExportsView} from './보고서변환';
const text=(value:unknown)=>typeof value==='string'?value:JSON.stringify(value,null,2)??'없음';
export function EventViewer({event}:{event:EventDetail}){return <article data-testid="event-detail"><p className="notice">{event.provider==='art-demo'?'장면 실험용 합성 사건 · 서버 요청 없음 · 실제 모델 요청 0':event.provider==='test-double'?'서버에 보존된 사건 · 테스트 대역 · 실제 모델 요청 0':event.provider==='openai'?'서버에 보존된 OpenAI 사건 · 요청 시도와 생성 성공은 다릅니다.':'서버에 보존된 코드 처리 사건'}</p><dl><dt>사건 ID</dt><dd>{event.event_id}</dd><dt>실행 / 버전</dt><dd>{event.run_id} / {event.control_revision}</dd><dt>보낸 역할</dt><dd>{event.actor}</dd><dt>받는 역할</dt><dd>{event.recipient??'없음'}</dd><dt>시각</dt><dd>{formatEventTime(event.occurred_at)} KST<br/>{event.occurred_at} (원본 UTC)</dd><dt>상태 / 유형</dt><dd>{event.status} / {event.type}</dd><dt>제공자</dt><dd>{event.provider??'코드 처리'}</dd></dl><h3>요약</h3><p>{event.summary}</p><h3>프롬프트 · 입력</h3><pre>{text(event.input)}</pre><h3>출력</h3><pre>{text(event.output)}</pre><h3>오류</h3><pre>{text(event.error)}</pre></article>}
export function ReportViewer({report,quality}:{report:Report;quality?:QualitySummary}){
 const selectionKey=JSON.stringify([report.report_id,report.sha256]);
 return <article data-testid="report-viewer">
  <dl><dt>보고서 ID</dt><dd>{report.report_id}</dd><dt>작성 시각</dt><dd>{formatEventTime(report.created_at)} KST · {report.created_at}</dd><dt>실행 / 버전</dt><dd>{report.run_id} / {report.control_revision}</dd></dl>
  <a className="download" href={report.download_url} download>Markdown 다운로드</a>
  <p className="report-reading-help">외부 Markdown 프로그램에서는 SVG가 코드로 보일 수 있습니다. 앱·HTML·PDF에서는 도식으로 표시합니다.</p>
  <ReportExportsView key={selectionKey} reportId={report.report_id} sourceHash={report.sha256}/>
  {!!quality?.warnings?.length&&<aside className="notice" data-testid="report-warnings"><h3>내용 검토 시 주의</h3><p>필수 형식·인용 검사 통과와 사실·인과관계의 정확성은 다릅니다.</p><ul>{quality.warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul></aside>}
  <ReportPresentationView key={selectionKey} reportId={report.report_id} sourceHash={report.sha256}/>
  <details className="report-markdown-source"><summary>Markdown 원문</summary><pre className="markdown">{report.markdown}</pre></details>
 </article>;
}
