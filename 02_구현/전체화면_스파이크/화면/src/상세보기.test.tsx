import {expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {EventViewer,ReportViewer} from './상세보기';
import type {EventDetail,Report} from './계약';
it('사건 상세는 원본 UTC, KST와 고정 ID를 보존하며 입력 HTML을 실행하지 않는다',()=>{
 const event={event_id:'fixed-7',run_id:'run-2',control_revision:3,occurred_at:'2026-11-06T01:02:03Z',actor:'planner',recipient:'editor',status:'completed',type:'plan.created',summary:'계획',provider:'art-demo',input:{prompt:'<script>alert(1)</script>'},output:{actual_model_requests:0},error:null} as EventDetail;
 const html=renderToStaticMarkup(<EventViewer event={event}/>);
 expect(html).toContain('fixed-7');expect(html).toContain('10:02:03');expect(html).toContain('2026-11-06T01:02:03Z');expect(html).toContain('장면 실험용 합성 사건');expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');
});
it('보고서는 정확한 보고서 ID와 전체 Markdown 및 실제 다운로드 URL을 제공한다',()=>{
 const report={report_id:'report-A',run_id:'run-A',control_revision:1,title:'합성 보고서',created_at:'2026-11-06T01:02:03Z',markdown:'# 원문\n<script>예시</script>\n마지막 문장',download_url:'/api/spike/reports/report-A/download',sha256:'hash'} as Report;
 const html=renderToStaticMarkup(<ReportViewer report={report}/>);
 expect(html).toContain('report-A');expect(html).toContain('href="/api/spike/reports/report-A/download"');expect(html).toContain('Markdown 다운로드');expect(html).toContain('마지막 문장');expect(html).not.toContain('<script>');
});
