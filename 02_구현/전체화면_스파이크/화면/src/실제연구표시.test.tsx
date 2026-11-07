import {afterEach,expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {App} from './앱';
import {EventViewer,ReportViewer} from './상세보기';
import type {Snapshot,EventDetail} from './계약';
const state=vi.hoisted(()=>({snapshot:null as Snapshot|null,popup:null as any,drawer:true,replay:null as any}));
vi.mock('react',async original=>{const r=await original<typeof import('react')>();return {...r,useReducer:(reducer:any,initial:any)=>r.useReducer(reducer,{...initial,popup:state.popup,drawer:state.drawer})};});
vi.mock('./실행기록',()=>({useJournal:()=>({snapshot:state.snapshot,refresh:vi.fn(),connection:'조회 연결됨',error:''})}));
vi.mock('./마을',()=>({Village:(p:any)=><canvas data-testid="village-canvas" data-mode={p.mode}/>}));
vi.mock('./기록재생연결',()=>({useRecordedReplay:()=>state.replay??({open:false,replay:false,archive:null,projection:null,at:0,runs:[],setOpen:vi.fn()})}));
const run={run_id:'A',question:'연구 질문',control_revision:1,status:'running' as const,phase:'researching',plan:[],draft:null,report_id:null,quality:{passed:false,reasons:['인용 검증 실패']},parent_report_id:null,created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:01Z',mock_requests:0,actual_model_requests:7};
it('좌상단 자료 표시는 실행중 실행자료·유휴 저장선택·조회전 미확인을 구별하고 조작을 추가하지 않는다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 state.snapshot={...actual,selected_source_mode:'local',current_run_id:'A',runs:[{...run,source_mode:'web'}]};
 let html=renderToStaticMarkup(<App/>);
 expect(html).toMatch(/data-testid="source-mode-indicator"[^>]*role="status"[^>]*>웹 검색<\/span>/);
 expect(html).not.toContain('<select');
 state.snapshot={...actual,selected_source_mode:'web',current_run_id:'A',runs:[{...run,status:'completed',source_mode:'local'}]};
 html=renderToStaticMarkup(<App/>);expect(html).toMatch(/data-testid="source-mode-indicator"[^>]*>웹 검색<\/span>/);
 state.snapshot=null;html=renderToStaticMarkup(<App/>);expect(html).toContain('자료 미확인');
});
it('기존 실행/큐와 과거 상세에 자료와 논리 검색·본문·관측 전송을 모델 수와 분리한다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 state.snapshot={...actual,current_run_id:'A',selected_source_mode:'web',runs:[{...run,source_mode:'web',web_usage:{search_attempts:2,search_limit:16,fetch_attempts:3,fetch_limit:24,observed_http_requests:6}}],queue:[{instruction_id:'q',text:'다음 질문',raw_text:'/큐 /DB 다음 질문',status:'queued',source_mode:'local'}]};
 state.popup={kind:'실행/큐'};let html=renderToStaticMarkup(<App/>);
 for(const label of ['논리 검색 시도 2 / 16','본문 수집 시도 3 / 24','관측 HTTP 요청 6','네이티브 내부','/큐 /DB 다음 질문','데이터셋'])expect(html).toContain(label);
 state.popup={kind:'history',runId:'A'};html=renderToStaticMarkup(<App/>);expect(html).toContain('자료</dt><dd>웹 검색');expect(html).toContain('관측 HTTP 요청 6');
});
it('재생 실행 팝업의 자료는 미래의 live 실행이 아니라 해당 시점의 실행을 따른다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});state.snapshot={...actual,current_run_id:'A',runs:[{...run,source_mode:'local'}]};
 state.replay={open:false,replay:true,archive:null,at:100,projection:{provider:'openai',run:{...run,source_mode:'web'},events:[],report:null,waiting:[]}};state.popup={kind:'실행/큐'};
 const html=renderToStaticMarkup(<App/>);expect(html).toContain('자료</dt><dd>웹 검색');expect(html).not.toContain('자료</dt><dd>데이터셋');
});
it('실제 사건 상세는 요청0이라고 단정하지 않고 원문·오류를 안전하게 보존한다',()=>{
 const network=vi.fn();vi.stubGlobal('fetch',network);
 const e:EventDetail={event_id:'safe-e',run_id:'A',seq:1,control_revision:1,occurred_at:run.created_at,type:'model.failed',actor:'planner',recipient:null,status:'failed',summary:'생성 실패',input:{prompt:'<script>safe input</script>'},output:'비밀 없는 긴 출력'.repeat(500),error:{code:'model_unavailable',message:'선택 모델을 사용할 수 없습니다.'},provider:'openai'};
 const html=renderToStaticMarkup(<EventViewer event={e}/>);
 expect(html).not.toContain('실제 모델 요청 0');expect(html).toContain('openai');expect(html).toContain('&lt;script&gt;safe input&lt;/script&gt;');expect(html).toContain(e.output as string);expect(html).toContain('선택 모델을 사용할 수 없습니다.');
 expect(renderToStaticMarkup(<EventViewer event={{...e,provider:'test-double'}}/>)).toContain('테스트 대역');
 expect(renderToStaticMarkup(<EventViewer event={{...e,provider:'art-demo'}}/>)).toContain('합성 사건');
 const report={report_id:'R',run_id:'A',control_revision:1,title:'연구 보고서',markdown:'# 실제 fixture 본문',sha256:'nonsecret-fixture',created_at:run.created_at,download_url:'/api/spike/reports/R/download'};
 const rh=renderToStaticMarkup(<ReportViewer report={report}/>);expect(rh).toContain('Markdown 다운로드');expect(rh).toContain(report.markdown);expect(rh).toContain(report.download_url);expect(network).not.toHaveBeenCalled();
});
const actual:Snapshot={protocol_version:1,provider:'openai',mode:'research',runs:[],events:[],waiting:[],queue:[],current_run_id:null,queue_paused:false,queue_pause_reason:null,high_watermark_seq:0,events_has_more:false,mock_requests:0,actual_model_requests:7};
afterEach(()=>{vi.unstubAllGlobals();state.drawer=true;state.popup=null;state.snapshot=null;state.replay=null;});
const failedRun={...run,status:'failed' as const,phase:'failed',quality:{passed:null,reasons:[]},actual_model_requests:26};
const failureReason={code:'model_error',message:'web: 사유 문구',actor:'researcher-4'};
it('실패한 실행의 사유는 실행/큐와 과거 상세에서 사건 상세를 열지 않아도 보이고 사유 필드가 없던 기록은 안내만 한다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 state.snapshot={...actual,current_run_id:'A',runs:[{...failedRun,failure:failureReason}]};
 state.popup={kind:'실행/큐'};let html=renderToStaticMarkup(<App/>);
 expect(html).toContain('실행 실패 사유 · researcher-4 · model_error: web: 사유 문구');
 state.popup={kind:'history',runId:'A'};html=renderToStaticMarkup(<App/>);
 expect(html).toContain('실행 실패 사유 · researcher-4 · model_error: web: 사유 문구');
 state.snapshot={...actual,current_run_id:'A',runs:[failedRun]};
 state.popup={kind:'실행/큐'};html=renderToStaticMarkup(<App/>);expect(html).toContain('실패 사유가 기록되지 않은 실행입니다');
 state.snapshot={...actual,current_run_id:'A',runs:[run]};
 html=renderToStaticMarkup(<App/>);expect(html).not.toContain('data-testid="run-failure"');
});
it('상한 필드가 없는 이전 웹 사용량도 undefined를 보이지 않는다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 state.snapshot={...actual,current_run_id:'A',runs:[{...run,source_mode:'web',web_usage:{search_attempts:10,fetch_attempts:9,observed_http_requests:19}}]};
 state.popup={kind:'실행/큐'};const html=renderToStaticMarkup(<App/>);
 expect(html).not.toContain('undefined');expect(html).toContain('논리 검색 시도 10');expect(html).toContain('본문 수집 시도 9');
});
it('실제 서버 루트는 실행 제어로 열고 실제 요청 수를 표시하되 모의 경고나 연결 성공 장식을 만들지 않는다',()=>{
 vi.stubGlobal('location',{hash:''});state.snapshot=actual;
 let html=renderToStaticMarkup(<App/>);
 expect(html).toContain('data-mode="control"');
 state.drawer=false;state.popup={kind:'실행/큐'};html=renderToStaticMarkup(<App/>);state.drawer=true;expect(html).toContain('OpenAI · 실제 모델 요청 7');
 expect(html).not.toContain('모의 응답');expect(html).not.toContain('테스트 대역');expect(html).not.toContain('실제 연결');
});
it('명시적 에셋 선택은 실제 서버에서도 합성 장면으로 남고 대역 경고와 해제 조작도 유지한다',()=>{
 vi.stubGlobal('location',{hash:'#/assets'});state.snapshot=actual;
 const network=vi.fn();vi.stubGlobal('fetch',network);
 let html=renderToStaticMarkup(<App/>);expect(html).toContain('data-mode="assets"');expect(html).toContain('합성 장면');
 vi.stubGlobal('location',{hash:'#/control'});state.snapshot={...actual,provider:'test-double',mode:'spike',actual_model_requests:0};state.popup={kind:'제어 도구'};
 html=renderToStaticMarkup(<App/>);expect(html).toContain('모의 응답');state.popup={kind:'실행/큐'};expect(renderToStaticMarkup(<App/>)).toContain('테스트 대역 · 실제 모델 요청 0');state.popup={kind:'제어 도구'};expect(html).toContain('현재 모두 완료');expect(html).toContain('실패 응답');expect(network).not.toHaveBeenCalled();
});
it('실제 실행 팝업은 예산·품질 사유와 취소를 제공하고 대역 해제 조작은 숨긴다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});state.snapshot={...actual,current_run_id:'A',runs:[run],budget:{attempt_count:7,max_attempts:40,remaining_attempts:33,spent_usd:0.0123,reserved_usd:0.03,max_usd:1,input_tokens:1000,output_tokens:200,unknown_count:1}};
 state.popup={kind:'실행/큐'};const html=renderToStaticMarkup(<App/>);
 for(const text of ['시도 7 / 40','남은 시도 33','0.0123','0.0300','1.0000','입력 1000','출력 200','불명 1','실제 청구','인용 검증 실패','현재 실행 취소'])expect(html).toContain(text);
 state.popup={kind:'제어 도구'};const controls=renderToStaticMarkup(<App/>);
 expect(controls).not.toContain('현재 모두 완료');expect(controls).not.toContain('실패 응답');expect(controls).toContain('현재 실행 취소');expect(controls).not.toMatch(/disabled="">현재 실행 취소/);
});

const MENUS=['장면 도구','실행/큐','과거 기록','사건 기록','제어 도구','안내'];
const REQUEST_TEXT='실제 모델 요청';
for(const [name,snap] of [['openai 연구',actual],['test-double',{...actual,provider:'test-double' as const,mode:'spike' as const,actual_model_requests:0}]] as const){
 it(`서랍(${name})은 제목과 여섯 메뉴만 순서대로 갖고 모드 전환·요청 수 줄이 없다`,()=>{
  vi.stubGlobal('location',{hash:'#/control'});state.snapshot=snap as Snapshot;state.popup=null;
  const html=renderToStaticMarkup(<App/>);
  const m=html.match(/<nav class="drawer" data-testid="drawer"[^>]*>([\s\S]*?)<\/nav>/);expect(m).not.toBeNull();
  const drawer=m![1];
  expect(drawer).toContain('<h2>연구 마을</h2>');
  expect([...drawer.matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map(x=>x[1])).toEqual(MENUS);
  expect(drawer).not.toContain('mode-options');expect(drawer).not.toContain('에셋 실험');expect(drawer).not.toContain('실행 제어');
  expect(drawer).not.toContain(REQUEST_TEXT);expect(drawer).not.toContain('<p>');
 });
}
it('실행/큐 팝업은 호출 예산 블록을 그대로 보여 준다',()=>{
 vi.stubGlobal('location',{hash:'#/control'});
 state.snapshot={...actual,budget:{attempt_count:7,max_attempts:40,remaining_attempts:33,spent_usd:0.0123,reserved_usd:0.03,max_usd:1,input_tokens:1000,output_tokens:200,unknown_count:1}};
 state.drawer=false;state.popup={kind:'실행/큐'};const html=renderToStaticMarkup(<App/>);
 expect(html).toContain('호출 예산');expect(html).toContain('시도 7 / 40 · 남은 시도 33');expect(html).toContain('OpenAI · 실제 모델 요청 7');
});
it('토글을 가리키던 문구는 주소 해시 안내로 바뀐다(장면 도구 팝업·안내 팝업·에셋 placeholder)',()=>{
 vi.stubGlobal('location',{hash:'#/control'});state.snapshot=actual;state.drawer=false;
 state.popup={kind:'장면 도구'};let html=renderToStaticMarkup(<App/>);
 expect(html).toContain('연출을 보려면 주소 끝을 #/assets로 바꿔 에셋 실험으로 들어가 주세요.');expect(html).not.toContain('메뉴에서 에셋 실험');
 state.popup={kind:'안내'};html=renderToStaticMarkup(<App/>);
 expect(html).toContain('주소 끝이 #/assets이면 합성 장면(에셋 실험), #/control이면 로컬 API와 보존된 기록에 연결된 실행 제어입니다.');expect(html).toContain('OpenAI · 실제 모델 요청 7');
 expect(html).not.toContain('에셋 실험은 합성 장면입니다.');
 vi.stubGlobal('location',{hash:'#/assets'});state.popup=null;html=renderToStaticMarkup(<App/>);
 expect(html).toContain('placeholder="연구 지시 · 주소 끝을 #/control로"');expect(html).not.toContain('메뉴에서 실행 제어');
});
