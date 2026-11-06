import {afterEach,expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {App} from './앱';
import {projectArchive} from './시간재생';
import type {RunArchive} from './시간계약';
const replayOverride=vi.hoisted(()=>({value:null as any}));
vi.mock('./기록재생연결',async original=>{const mod=await original<typeof import('./기록재생연결')>();return {useRecordedReplay:(snapshot:any)=>replayOverride.value??mod.useRecordedReplay(snapshot)};});
vi.mock('./마을',()=>({Village:(props:any)=> <canvas data-testid="village-canvas" data-code={props.code?.eventId} data-report={props.latestReport?.id} data-at={props.clock?.at_ms}/>}));
vi.mock('./실행기록',()=>({useJournal:()=>({snapshot:{protocol_version:1,provider:'test-double',mode:'spike',runs:[],events:[],waiting:[],queue:[],current_run_id:null,queue_paused:false,queue_pause_reason:null,high_watermark_seq:0,events_has_more:false,mock_requests:0,actual_model_requests:0},refresh:vi.fn(),connection:'test',error:''})}));
it('앱은 드로어·입력·팝업이 공유할 단일 유효 높이와 실제 크기 변경 콜백을 연결한다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 replayOverride.value={open:true,runId:null,archive:null,at:0,replay:false,playing:false,projection:null,loading:false,error:'',setOpen:vi.fn(),select:vi.fn(),seek:vi.fn(),play:vi.fn(),stage:vi.fn(),live:vi.fn(),retry:vi.fn()};
 const html=renderToStaticMarkup(<App/>);
 expect(html).toContain('--timeline-height:360px');
 expect(html).toContain('aria-valuenow="360"');
 expect(html).toContain('aria-valuemax="738"');
 expect(html).toContain('data-testid="command-input"');
});
afterEach(()=>{vi.unstubAllGlobals();replayOverride.value=null;});
it('에셋 실험의 기존 독립 연출에는 live 기록 clock을 강제하지 않는다',()=>{
 vi.stubGlobal('location',{hash:'#/assets'});
 replayOverride.value={open:false,runId:null,archive:{run:{run_id:'A'},events:[],spans:[],started_at:'2026-11-06T00:00:00Z'},at:8000,replay:false,playing:false,projection:null,loading:false,error:'',setOpen:vi.fn(),select:vi.fn(),seek:vi.fn(),play:vi.fn(),stage:vi.fn(),live:vi.fn(),retry:vi.fn()};
 const html=renderToStaticMarkup(<App/>);
 expect(html).not.toContain('data-at="8000"');
});
it('재생 장면의 코드 사건은 archive 전체의 미래 사건이 아니라 해당 cursor까지의 사건이다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 const run={run_id:'A',question:'Q',control_revision:1,status:'completed' as const,phase:'submitted',plan:[],draft:'future draft',report_id:'future-report',quality:{passed:true,reasons:[]},parent_report_id:null,created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:05Z',mock_requests:0,actual_model_requests:0 as const};
 const common={run_id:'A',control_revision:1,actor:'planner',recipient:null,status:'completed',summary:'event',input:null,output:null,error:null,provider:'test-double' as const};
 const archive:RunArchive={run,revisions:[run],protocol_version:1,provider:'test-double',started_at:run.created_at,ended_at:run.updated_at,recorded_at:run.updated_at,duration_ms:5000,has_measured_spans:false,spans:[],truncated:false,high_watermark_seq:2,events:[{...common,event_id:'early-instruction',seq:1,type:'instruction.received',input:{text:'Q'},occurred_at:run.created_at},{...common,event_id:'future-plan',seq:2,type:'plan.created',output:[],occurred_at:'2026-11-06T00:00:04Z'}]};
 replayOverride.value={open:true,runId:'A',archive,at:1000,replay:true,playing:false,projection:projectArchive(archive,1000),loading:false,error:'',setOpen:vi.fn(),select:vi.fn(),seek:vi.fn(),play:vi.fn(),stage:vi.fn(),live:vi.fn(),retry:vi.fn()};
 const html=renderToStaticMarkup(<App/>);
 expect(html).toContain('data-code="early-instruction"');expect(html).not.toContain('data-code="future-plan"');expect(html).not.toContain('data-report="future-report"');expect(html).toContain('data-at="1000"');
});
it('대역 서버에서 실제 아카이브를 재생해도 제공자와 요청 수는 재생 원본을 따른다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 const run={run_id:'A',question:'Q',control_revision:1,status:'running' as const,phase:'planning',plan:[],draft:null,report_id:null,quality:{passed:null,reasons:[]},parent_report_id:null,created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:05Z',mock_requests:0,actual_model_requests:1};
 const archive:RunArchive={run,revisions:[run],protocol_version:1,provider:'openai',started_at:run.created_at,ended_at:null,recorded_at:run.updated_at,duration_ms:5000,has_measured_spans:false,spans:[],truncated:false,high_watermark_seq:2,events:[{event_id:'instruction',run_id:'A',control_revision:1,actor:'planner',recipient:null,status:'completed',summary:'Q',input:{text:'Q'},output:null,error:null,provider:null,seq:1,type:'instruction.received',occurred_at:run.created_at},{event_id:'started',run_id:'A',control_revision:1,actor:'planner',recipient:null,status:'started',summary:'model.started',input:{prompt:'안전한 테스트 입력'},output:null,error:null,provider:'openai',seq:2,type:'model.started',occurred_at:'2026-11-06T00:00:01Z'}]};
 replayOverride.value={open:true,runId:'A',archive,at:1500,replay:true,playing:false,projection:projectArchive(archive,1500),loading:false,error:'',setOpen:vi.fn(),select:vi.fn(),seek:vi.fn(),play:vi.fn(),stage:vi.fn(),live:vi.fn(),retry:vi.fn()};
 const html=renderToStaticMarkup(<App/>);expect(html).not.toContain('모의 응답');expect(html).toContain('기록 재생 중');
});
it('실제 앱에 입력 아래 타임라인 손잡이를 연결하고 복원된 기록 모드는 명령을 잠근다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 vi.stubGlobal('sessionStorage',{getItem:()=>JSON.stringify({runId:'completed-A',at:1000}),setItem:vi.fn(),removeItem:vi.fn()});
 const html=renderToStaticMarkup(<App/>);
 expect(html).toContain('data-testid="timeline-toggle"');
 expect(html.indexOf('data-testid="command-input"')).toBeLessThan(html.indexOf('data-testid="timeline-toggle"'));
 expect(html).toContain('읽기 전용 기록');expect(html).toContain('실시간 복귀');
 expect(html).toMatch(/data-testid="command-submit"[^>]*disabled/);
 expect(html).toContain('>기록 재생 중</button>');
 expect(html).toContain('village-canvas');
});
