import {afterEach,expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {App} from './앱';
import {projectArchive} from './시간재생';
import type {RunArchive} from './시간계약';
import {PastRecordList} from './과거기록목록';
import {DeleteButton,DeleteConfirm} from './실행삭제.tsx';
// Explicit no-DOM hook unit harness: executes App's real command handlers, not HTTP/E2E.
const hooks=vi.hoisted(()=>({active:false,cursor:0,slots:[] as any[],snapshot:null as any,refresh:vi.fn()}));
vi.mock('react',async original=>{
 const r=await original<typeof import('react')>();
 function state(initial:any){const i=hooks.cursor++;if(!(i in hooks.slots))hooks.slots[i]=typeof initial==='function'?initial():initial;return [hooks.slots[i],(next:any)=>{hooks.slots[i]=typeof next==='function'?next(hooks.slots[i]):next;}];}
 return {...r,useState:(initial:any)=>hooks.active?state(initial):r.useState(initial),useReducer:(reducer:any,initial:any)=>{if(!hooks.active)return r.useReducer(reducer,initial);const [s,set]=state(initial);return [s,(action:any)=>set((old:any)=>reducer(old,action))];},useRef:(initial:any)=>hooks.active?state(()=>({current:initial}))[0]:r.useRef(initial),useMemo:(fn:any,deps:any)=>hooks.active?fn():r.useMemo(fn,deps),useEffect:(fn:any,deps:any)=>{if(!hooks.active)r.useEffect(fn,deps);},useLayoutEffect:(fn:any,deps:any)=>{if(!hooks.active)r.useLayoutEffect(fn,deps);}};
});
const replayOverride=vi.hoisted(()=>({value:null as any}));
vi.mock('./기록재생연결',async original=>{const mod=await original<typeof import('./기록재생연결')>();return {useRecordedReplay:(snapshot:any)=>replayOverride.value??mod.useRecordedReplay(snapshot)};});
vi.mock('./마을',()=>({Village:(props:any)=> <canvas data-testid="village-canvas" data-code={props.code?.eventId} data-report={props.latestReport?.id} data-at={props.clock?.at_ms}/>}));
vi.mock('./실행기록',()=>({useJournal:()=>({snapshot:hooks.active?hooks.snapshot:{protocol_version:1,provider:'test-double',mode:'spike',runs:[],events:[],waiting:[],queue:[],current_run_id:null,queue_paused:false,queue_pause_reason:null,high_watermark_seq:0,events_has_more:false,mock_requests:0,actual_model_requests:0},refresh:hooks.refresh,connection:'test',error:''})}));
it('재생 인디케이터는 첫 지시 전 미확인이고 과거 지시의 웹 자료를 최종 데이터셋과 구별한다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 const run={run_id:'A',source_mode:'local' as const,question:'Q',control_revision:1,status:'completed' as const,phase:'submitted',plan:[],draft:null,report_id:null,quality:{passed:null,reasons:[]},parent_report_id:null,created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:02Z',mock_requests:0,actual_model_requests:0};
 const archive:RunArchive={protocol_version:1,high_watermark_seq:1,ended_at:run.updated_at,recorded_at:run.updated_at,has_measured_spans:false,truncated:false,run,revisions:[run],provider:'test-double',started_at:run.created_at,events:[{event_id:'i',run_id:'A',control_revision:1,actor:'planner',recipient:null,status:'completed',summary:'Q',input:{text:'/웹 Q',source_mode:'web'},output:null,error:null,provider:null,seq:1,type:'instruction.received',occurred_at:'2026-11-06T00:00:01Z'}],spans:[],duration_ms:2000};
 replayOverride.value={open:false,archive,at:0,replay:true,projection:projectArchive(archive,0)};
 expect(renderToStaticMarkup(<App/>)).toMatch(/data-testid="source-mode-indicator"[^>]*>자료 미확인<\/span>/);
 replayOverride.value={...replayOverride.value,at:1500,projection:projectArchive(archive,1500)};
 expect(renderToStaticMarkup(<App/>)).toMatch(/data-testid="source-mode-indicator"[^>]*>웹 검색<\/span>/);
});
it('앱은 드로어·입력·팝업이 공유할 단일 유효 높이와 실제 크기 변경 콜백을 연결한다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 replayOverride.value={open:true,runId:null,archive:null,at:0,replay:false,playing:false,projection:null,loading:false,error:'',setOpen:vi.fn(),select:vi.fn(),seek:vi.fn(),play:vi.fn(),stage:vi.fn(),live:vi.fn(),retry:vi.fn()};
 const html=renderToStaticMarkup(<App/>);
 expect(html).toContain('--timeline-height:360px');
 expect(html).toContain('aria-valuenow="360"');
 expect(html).toContain('aria-valuemax="738"');
 expect(html).toContain('data-testid="command-input"');
});
it('과거 기록 팝업은 새 목록을 쓰고 자세히 보기·기록 재생을 기존 동작에 연결한다',()=>{
  hooks.active=true;
  const mk=(id:string,at:string)=>({run_id:id,question:`질문 ${id}`,control_revision:1,status:'completed',phase:'submitted',plan:[],draft:null,report_id:null,quality:{passed:null,reasons:[]},parent_report_id:null,created_at:at,updated_at:at,mock_requests:0,actual_model_requests:0});
  hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:1,runs:[mk('A','2026-11-06T00:00:00Z'),mk('B','2026-11-07T00:00:00Z')],events:[],waiting:[],queue:[],current_run_id:'B'};
  vi.stubGlobal('location',{hash:'#/control'});
  const select=vi.fn(),live=vi.fn();
  replayOverride.value={open:false,runId:'A',replay:true,archive:null,projection:null,at:0,loading:false,error:'',setOpen:vi.fn(),select,live,seek:vi.fn(),play:vi.fn(),stage:vi.fn(),retry:vi.fn()};
  let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='menu-toggle').props.onClick();
  tree=commandRender();findNode(tree,n=>n.type==='button'&&n.props.children==='과거 기록').props.onClick();
  tree=commandRender();
  const list=findNode(tree,n=>n.type===PastRecordList);
  expect(list.props.runs).toHaveLength(2);expect(list.props.currentRunId).toBe('B');expect(list.props.replayRunId).toBe('A');
  list.props.onReplay('A');expect(select).toHaveBeenCalledWith('A');
  tree=commandRender();expect(findNode(tree,n=>n.type===PastRecordList)).toBeUndefined();
  hooks.cursor=0;
  tree=commandRender();findNode(tree,n=>n.props['data-testid']==='menu-toggle').props.onClick();
  tree=commandRender();findNode(tree,n=>n.type==='button'&&n.props.children==='과거 기록').props.onClick();
  tree=commandRender();findNode(tree,n=>n.type===PastRecordList).props.onDetail('B');
  tree=commandRender();
  expect(findNode(tree,n=>n.type===PastRecordList)).toBeUndefined();
  expect(findNode(tree,n=>n.type==='button'&&n.props.children==='목록으로')).toBeDefined();
});
afterEach(()=>{vi.unstubAllGlobals();replayOverride.value=null;hooks.active=false;hooks.slots=[];hooks.cursor=0;hooks.refresh.mockReset();});
function findNode(node:any,predicate:(p:any)=>boolean):any{
 if(!node||typeof node!=='object')return undefined;
 if(node.props&&predicate(node))return node;
 for(const child of [node.props?.children].flat(Infinity)){const found=findNode(child,predicate);if(found)return found;}
}
function commandRender(){hooks.cursor=0;return App();}
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
it.each([['web','local'],[undefined,'web']] as const)('일반 질문의 최초 %s 자료는 이후 %s 폴링으로 재해석하지 않는다',async(initial,ambient)=>{
 hooks.active=true;hooks.snapshot=initial===undefined?null:{provider:'openai',mode:'research',selected_source_mode:initial,high_watermark_seq:1,runs:[],events:[],waiting:[],queue:[],current_run_id:null};
 vi.stubGlobal('location',{hash:'#/control'});replayOverride.value={open:false,replay:false,archive:null,projection:null,at:0};
 const fetcher=vi.fn().mockRejectedValue(new Error('explicit unit transport failure'));vi.stubGlobal('fetch',fetcher);
 let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'접두어 없는 질문'}});tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();
 const first=JSON.parse(fetcher.mock.calls[0][1].body);expect(first.source_mode).toBe(initial);if(initial===undefined)expect(first).not.toHaveProperty('source_mode');
 hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:ambient,high_watermark_seq:5,runs:[],events:[],waiting:[],queue:[],current_run_id:null};
 tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
});
it('단독 /웹 ack는 선택만 알리고 늦은 GET/이전 ack가 확인한 선택이나 초안을 되돌리지 않는다',async()=>{
 hooks.active=true;hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:1,runs:[],events:[],waiting:[],queue:[],current_run_id:null};hooks.refresh.mockResolvedValue(undefined);
 vi.stubGlobal('location',{hash:'#/control'});replayOverride.value={open:false,replay:false,archive:null,projection:null,at:0};
 const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'mode_change',mode:'immediate',status:'applied',source_mode:'web',state_seq:10}))).mockResolvedValueOnce(new Response(JSON.stringify({kind:'mode_change',mode:'immediate',status:'applied',source_mode:'local',state_seq:8})));vi.stubGlobal('fetch',fetcher);
 let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'/웹'}});tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();
 tree=commandRender();expect(findNode(tree,n=>n.props['data-testid']==='source-mode-indicator').props.children).toBe('웹 검색');
 const notice=findNode(tree,n=>n.props.className==='composer-notice');expect(notice.props.children[0]).toContain('연구를 시작하거나 완료한 것이 아닙니다');expect(fetcher).toHaveBeenCalledTimes(1);
 hooks.snapshot={...hooks.snapshot,high_watermark_seq:9};tree=commandRender();expect(findNode(tree,n=>n.props['data-testid']==='source-mode-indicator').props.children).toBe('웹 검색');
 findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'/DB'}});tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();tree=commandRender();expect(findNode(tree,n=>n.props['data-testid']==='source-mode-indicator').props.children).toBe('웹 검색');expect(findNode(tree,n=>n.props.className==='composer-notice').props.children[0]).toContain('과거 응답');
});
it('종료 직전 실행의 늦은 GET은 더 최신 단독 선택 ack 인디케이터를 되돌리지 않는다',async()=>{
 hooks.active=true;const old={run_id:'old',source_mode:'local',status:'running',question:'이전 연구',control_revision:1,plan:[]};
 hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:1,runs:[old],events:[],waiting:[],queue:[],current_run_id:'old'};hooks.refresh.mockResolvedValue(undefined);
 vi.stubGlobal('location',{hash:'#/control'});replayOverride.value={open:false,replay:false,archive:null,projection:null,at:0};
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({kind:'mode_change',mode:'immediate',status:'applied',source_mode:'web',state_seq:10})));vi.stubGlobal('fetch',fetcher);
 let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'/웹'}});tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();
 tree=commandRender();expect(findNode(tree,n=>n.props['data-testid']==='source-mode-indicator').props.children).toBe('웹 검색');
});
it('다른 자료 잠금은 원문 초안과 자료를 보존하고 /큐 또는 다음 연구 복구를 안내한다',async()=>{
 hooks.active=true;hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:1,runs:[],events:[],waiting:[],queue:[],current_run_id:null};vi.stubGlobal('location',{hash:'#/control'});replayOverride.value={open:false,replay:false,archive:null,projection:null,at:0};
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({error:{code:'source_mode_locked',message:'자료 고정'}}),{status:409}));vi.stubGlobal('fetch',fetcher);
 let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'/웹 질문'}});tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();tree=commandRender();
 expect(findNode(tree,n=>n.props['data-testid']==='command-input').props.value).toBe('/웹 질문');expect(findNode(tree,n=>n.props['data-testid']==='source-mode-indicator').props.children).toBe('데이터셋');const message=findNode(tree,n=>n.props.className==='composer-notice').props.children[0];expect(message).toContain('source_mode_locked');expect(message).toContain('/큐');expect(message).toContain('다음 연구');expect(fetcher).toHaveBeenCalledTimes(1);
});
it('명령 핸들러는 /큐 /웹 원문과 실패한 자료·ID를 배경 폴링 후에도 재전송하고 새 초안을 보존한다',async()=>{
 hooks.active=true;hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:1,runs:[],events:[],waiting:[],queue:[],current_run_id:null};hooks.refresh.mockResolvedValue(undefined);
 vi.stubGlobal('location',{hash:'#/control'});
 replayOverride.value={open:false,replay:false,archive:null,projection:null,at:0,setOpen:vi.fn()};
 const fetcher=vi.fn().mockRejectedValueOnce(new Error('unit network failure')).mockResolvedValueOnce(new Response(JSON.stringify({kind:'research',mode:'queued',status:'queued',source_mode:'web',state_seq:4})));
 vi.stubGlobal('fetch',fetcher);
 let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'  /큐 /웹 질문  '}});
 tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();
 expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({text:'  /큐 /웹 질문  ',source_mode:'web'});
 hooks.snapshot={...hooks.snapshot,selected_source_mode:'web',high_watermark_seq:3};
 tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});
 tree=commandRender();findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'보내는 동안 쓴 새 초안'}});commandRender();await settle();
 expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
 tree=commandRender();expect(findNode(tree,n=>n.props['data-testid']==='command-input').props.value).toBe('보내는 동안 쓴 새 초안');
});
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

it('에셋 모드의 전송 시도는 주소 해시로 실행 제어에 들어가라고 안내하고 서버로 보내지 않는다',async()=>{
 hooks.active=true;hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:1,runs:[],events:[],waiting:[],queue:[],current_run_id:null};
 vi.stubGlobal('location',{hash:'#/assets'});replayOverride.value={open:false,replay:false,archive:null,projection:null,at:0};
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='command-input').props.onChange({target:{value:'질문'}});tree=commandRender();findNode(tree,n=>n.type==='form').props.onSubmit({preventDefault:vi.fn()});await settle();tree=commandRender();
 expect(findNode(tree,n=>n.props.className==='composer-notice').props.children[0]).toBe('장면 실험에서는 전송하지 않습니다. 주소 끝을 #/control로 바꿔 실행 제어로 들어가 주세요.');expect(fetcher).not.toHaveBeenCalled();
});

// ---- 실행 삭제 연결 (SPEC 28.5) ----
const delCounts={events:12,calls:4,activity_spans:6,requests:3,reports:1,report_files:1,export_files:2,web_sources:5,web_reads:7};
const delRun=(id:string,at:string,status='completed')=>({run_id:id,question:`질문 ${id}`,control_revision:1,status,phase:'submitted',plan:[],draft:null,report_id:null,quality:{passed:null,reasons:[]},parent_report_id:null,created_at:at,updated_at:at,mock_requests:0,actual_model_requests:0});
function deleteFixture(replayRunId:string|null){
  hooks.active=true;
  hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:1,runs:[delRun('A','2026-11-06T00:00:00Z'),delRun('B','2026-11-07T00:00:00Z')],events:[],waiting:[],queue:[],current_run_id:'B'};
  vi.stubGlobal('location',{hash:'#/control'});
  const live=vi.fn(),deleted=vi.fn();
  replayOverride.value={open:false,runId:replayRunId,replay:replayRunId!==null,archive:null,projection:null,at:0,loading:false,error:'',setOpen:vi.fn(),select:vi.fn(),live,deleted,seek:vi.fn(),play:vi.fn(),stage:vi.fn(),retry:vi.fn()};
  const fetcher=vi.fn().mockImplementation(async(path:string,init?:any)=>init?.method==='POST'
    ?new Response(JSON.stringify({status:'deleted',run_id:'A',counts:delCounts,warnings:['변환 파일 1개를 지우지 못했습니다.'],current_run_id:'B'}))
    :new Response(JSON.stringify({run_id:'A',deletable:true,blockers:[],counts:delCounts})));
  vi.stubGlobal('fetch',fetcher);
  return {live,deleted,fetcher};
}
function openPastRecords(){
  let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='menu-toggle').props.onClick();
  tree=commandRender();findNode(tree,n=>n.type==='button'&&n.props.children==='과거 기록').props.onClick();
  return commandRender();
}
it('삭제: 목록에서 시작하면 서버 개수로 확인 상태가 되고, 확인하면 같은 요청 ID로 POST한다',async()=>{
  const {fetcher}=deleteFixture(null);
  let tree=openPastRecords();
  findNode(tree,n=>n.type===PastRecordList).props.onDeleteStart('A');await settle();
  tree=commandRender();
  expect(findNode(tree,n=>n.type===PastRecordList).props.deleteFlow).toMatchObject({phase:'ready',runId:'A'});
  expect(fetcher.mock.calls[0]).toEqual(['/api/spike/runs/A/delete-preview',undefined]);
  findNode(tree,n=>n.type===PastRecordList).props.onDeleteConfirm();await settle();
  const post=fetcher.mock.calls[1];
  expect(post[0]).toBe('/api/spike/runs/A/delete');
  const body=JSON.parse(post[1].body);
  expect(body.confirm_run_id).toBe('A');expect(body.client_request_id).toBeTruthy();
  expect(hooks.refresh).toHaveBeenCalled();
  tree=commandRender();
  expect(findNode(tree,n=>n.type===PastRecordList)).toBeDefined();
  expect(findNode(tree,n=>n.type===PastRecordList).props.deleteFlow.phase).toBe('done');
});
it('삭제 성공: 재생 중이던 실행이면 실시간으로 복귀하고, 다른 실행이면 복귀하지 않는다',async()=>{
  for(const [replayRunId,expected] of [['A',1],['B',0]] as const){
    const {live,deleted}=deleteFixture(replayRunId);
    let tree=openPastRecords();
    findNode(tree,n=>n.type===PastRecordList).props.onDeleteStart('A');await settle();
    tree=commandRender();findNode(tree,n=>n.type===PastRecordList).props.onDeleteConfirm();await settle();
    expect(live).toHaveBeenCalledTimes(expected);
    expect(deleted).toHaveBeenCalledExactlyOnceWith('A');
    hooks.slots=[];hooks.cursor=0;replayOverride.value=null;hooks.refresh.mockReset();
  }
});
it.each([null,'B'])('삭제 성공 알림: 다른 실행 %s 재생 여부와 무관하게 개수 요약과 경고를 보인다',async replayRunId=>{
  const {live}=deleteFixture(replayRunId);
  let tree=openPastRecords();
  findNode(tree,n=>n.type===PastRecordList).props.onDeleteStart('A');await settle();
  tree=commandRender();findNode(tree,n=>n.type===PastRecordList).props.onDeleteConfirm();await settle();
  tree=commandRender();
  const notice=findNode(tree,n=>n.props.className==='composer-notice');
  expect(notice).toBeDefined();
  expect(notice.props.children[0]).toBe('실행을 삭제했습니다. 사건 12건 · 보고서 1건 · 변환 파일 2개 · 웹 자료 5건 변환 파일 1개를 지우지 못했습니다.');
  expect(live).not.toHaveBeenCalled();
});
it('삭제 실패: 오류 문구를 확인 상태에 두고 같은 요청 ID로 다시 보낸다',async()=>{
  const {fetcher}=deleteFixture(null);
  fetcher.mockImplementation(async(path:string,init?:any)=>init?.method==='POST'
    ?new Response(JSON.stringify({error:{code:'run_running',message:'실행 중'}}),{status:409})
    :new Response(JSON.stringify({run_id:'A',deletable:true,blockers:[],counts:delCounts})));
  let tree=openPastRecords();
  findNode(tree,n=>n.type===PastRecordList).props.onDeleteStart('A');await settle();
  tree=commandRender();findNode(tree,n=>n.type===PastRecordList).props.onDeleteConfirm();await settle();
  tree=commandRender();
  const flow=findNode(tree,n=>n.type===PastRecordList).props.deleteFlow;
  expect(flow.phase).toBe('error');expect(flow.message).toContain('진행 중인 실행');
  findNode(tree,n=>n.type===PastRecordList).props.onDeleteConfirm();await settle();
  const ids=fetcher.mock.calls.filter(c=>c[1]?.method==='POST').map(c=>JSON.parse(c[1].body).client_request_id);
  expect(ids).toHaveLength(2);expect(ids[0]).toBe(ids[1]);
  expect(hooks.refresh).not.toHaveBeenCalled();
});
it('실행 상세에도 같은 삭제 진입점과 확인이 있고, 성공하면 과거 기록 목록으로 돌아간다',async()=>{
  deleteFixture(null);
  let tree=openPastRecords();
  findNode(tree,n=>n.type===PastRecordList).props.onDetail('A');
  tree=commandRender();
  expect(findNode(tree,n=>n.type===PastRecordList)).toBeUndefined();
  findNode(tree,n=>n.type===DeleteButton).props.onStart('A');await settle();
  tree=commandRender();
  const confirm=findNode(tree,n=>n.type===DeleteConfirm);
  expect(confirm.props.flow).toMatchObject({phase:'ready',runId:'A'});
  confirm.props.onConfirm();await settle();
  tree=commandRender();
  expect(findNode(tree,n=>n.type===PastRecordList)).toBeDefined();
  expect(findNode(tree,n=>n.type==='button'&&n.props.children==='목록으로')).toBeUndefined();
});
it('사건 기록 목록은 run.deleted 흔적 사건을 숨긴다',()=>{
  hooks.active=true;
  const ev=(id:string,type:string,seq:number)=>({event_id:id,run_id:id==='tomb'?'deletions':'B',control_revision:1,seq,occurred_at:'2026-11-08T00:00:00Z',type,actor:'dispatcher',recipient:'x',status:'applied',summary:`요약 ${id}`});
  hooks.snapshot={provider:'openai',mode:'research',selected_source_mode:'local',high_watermark_seq:3,runs:[delRun('B','2026-11-07T00:00:00Z')],events:[ev('e1','plan.created',1),ev('tomb','run.deleted',3)],waiting:[],queue:[],current_run_id:'B'};
  vi.stubGlobal('location',{hash:'#/control'});replayOverride.value={open:false,replay:false,archive:null,projection:null,at:0};
  let tree=commandRender();findNode(tree,n=>n.props['data-testid']==='menu-toggle').props.onClick();
  tree=commandRender();findNode(tree,n=>n.type==='button'&&n.props.children==='사건 기록').props.onClick();
  tree=commandRender();
  const html=renderToStaticMarkup(findNode(tree,n=>n.props.className==='popup-body'));
  expect(html).toContain('요약 e1');
  expect(html).not.toContain('요약 tomb');
});
