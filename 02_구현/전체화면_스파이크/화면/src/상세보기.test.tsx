import {expect,it} from 'vitest';
it('같은 보고서 안에서 HTML과 PDF 생성 제어를 제공하며 조회 전 다운로드는 없다',()=>{
 const report={report_id:'A',run_id:'r',control_revision:1,title:'제목',created_at:'2026-11-06T01:02:03Z',markdown:'# 본문',download_url:'/md',sha256:'hash'};
 const html=renderToStaticMarkup(<ReportViewer report={report}/>);
 expect(html).toContain('data-testid="report-exports"');
 expect(html).toContain('data-testid="export-html-create"');
 expect(html).toContain('data-testid="export-pdf-create"');
 expect(html).not.toContain('export-html-download');
 expect(html).toContain('변환 상태를 조회');
});
import {renderToStaticMarkup} from 'react-dom/server';
import {EventViewer,ReportViewer} from './상세보기';
import type {EventDetail,Report} from './계약';
it('Markdown 다운로드 옆에서 외부 프로그램의 SVG 코드 표시 한계를 설명한다',()=>{
 const report={report_id:'report-A',run_id:'run-A',control_revision:1,title:'보고서',created_at:'2026-11-06T01:02:03Z',markdown:'# 본문',download_url:'/download',sha256:'hash'} as Report;
 const html=renderToStaticMarkup(<ReportViewer report={report}/>);
 expect(html).toContain('외부 Markdown 프로그램에서는 SVG가 코드로 보일 수 있습니다');
 expect(html).toContain('앱·HTML·PDF에서는 도식으로 표시합니다');
});
it('사건 상세는 원본 UTC, KST와 고정 ID를 보존하며 입력 HTML을 실행하지 않는다',()=>{
 const event={event_id:'fixed-7',run_id:'run-2',control_revision:3,occurred_at:'2026-11-06T01:02:03Z',actor:'planner',recipient:'editor',status:'completed',type:'plan.created',summary:'계획',provider:'art-demo',input:{prompt:'<script>alert(1)</script>'},output:{actual_model_requests:0},error:null} as EventDetail;
 const html=renderToStaticMarkup(<EventViewer event={event}/>);
 expect(html).toContain('fixed-7');expect(html).toContain('10:02:03');expect(html).toContain('2026-11-06T01:02:03Z');expect(html).toContain('장면 실험용 합성 사건');expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');
});
it('보고서는 정확한 보고서 ID와 전체 Markdown 및 실제 다운로드 URL을 제공한다',()=>{
 const report={report_id:'report-A',run_id:'run-A',control_revision:1,title:'합성 보고서',created_at:'2026-11-06T01:02:03Z',markdown:'# 원문\n<script>예시</script>\n마지막 문장',download_url:'/api/spike/reports/report-A/download',sha256:'hash'} as Report;
 const html=renderToStaticMarkup(<ReportViewer report={report}/>);
 expect(html).toContain('report-A');expect(html).toContain('href="/api/spike/reports/report-A/download"');expect(html).toContain('Markdown 다운로드');expect(html).toContain('마지막 문장');expect(html).toContain('<details class="report-markdown-source"><summary>Markdown 원문</summary>');expect(html).toContain('data-testid="report-presentation"');expect(html).not.toContain('<script>');
});
it('형식 검사 통과와 별개로 보고서의 인용 품질 경고를 숨기지 않는다',()=>{
 const report={report_id:'report-A',run_id:'run-A',control_revision:1,title:'보고서',created_at:'2026-11-06T01:02:03Z',markdown:'# 본문',download_url:'/download',sha256:'hash'} as Report;
 const html=renderToStaticMarkup(<ReportViewer report={report} quality={{passed:true,reasons:[],warnings:['자동 인용 검사는 의미적 지지를 증명하지 않습니다.','조사관 사이에 중복 읽기가 있습니다.']}}/>);
 expect(html).toContain('의미적 지지를 증명하지 않습니다');
 expect(html).toContain('중복 읽기');
 expect(html).toContain('Markdown 다운로드');
});
