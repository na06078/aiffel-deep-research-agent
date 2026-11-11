import {expect,it,vi,afterEach} from 'vitest';
const eligible={run_id:'run/A',control_revision:1,source_mode:'local' as const,status:'blocked' as const,quality:{passed:false,reasons:['semantic_review_failed'],semantic_review:{통과:false,문제:[{유형:'인과',설명:'근거 범위 초과',본문발췌:'기존 주장',출처:['원문']}],검토요약:'보완'}}};
it('현재 local 의미 검토 실패만 명시적인 1회 보완 버튼을 제공한다',()=>{
 const html=renderToStaticMarkup(<RunFailureNotice run={eligible} readOnly={false}/>);
 expect(html).toContain('검토 지적 문장만1회 보완');
 expect(html).toContain('최대 2회');
});
import {renderToStaticMarkup} from 'react-dom/server';
import {RunFailureNotice} from './실행실패';
it('보완 범위 초과 판정은 한국어 사유로 표시한다',()=>{
 const run={...eligible,quality:{...eligible.quality,semantic_review:{...eligible.quality.semantic_review,문제:[{유형:'scope_expansion',설명:'새 원인 추가',본문발췌:'추가한 주장',출처:[]}]}}};
 expect(renderToStaticMarkup(<RunFailureNotice run={run}/>)).toContain('보완 허용 범위 초과');
});
it('이전 기록·웹·불명 판정·기시도·잘못된 식별은 보완을 노출하지 않는다',()=>{
 const cases=[{...eligible,report_id:'already-published'}, {...eligible,quality:{...eligible.quality,reasons:['semantic_review_failed','semantic_review_invalid']}}, {...eligible,quality:{...eligible.quality,reasons:['static_failed']}}, {...eligible,source_mode:'web'}, {...eligible,source_mode:undefined}, {...eligible,run_id:''}, {...eligible,control_revision:0}, {...eligible,control_revision:undefined}, {...eligible,control_revision:1.5}, {...eligible,status:'completed'}, {...eligible,quality:{...eligible.quality,reasons:['semantic_review_invalid']}}, {...eligible,quality:{...eligible.quality,semantic_review:{...eligible.quality.semantic_review,통과:true}}}, ...[0,3].map(n=>({...eligible,quality:{...eligible.quality,semantic_review:{...eligible.quality.semantic_review,문제:Array(n).fill(eligible.quality.semantic_review.문제[0])}}})), ...['pending','applied','passed','failed','completed'].map(status=>({...eligible,correction_state:{status,source_revision:1,max_model_calls:2}}))];
 for(const run of cases)expect(renderToStaticMarkup(<RunFailureNotice run={run as any} readOnly={false}/>)).not.toContain('data-testid="run-correction"');
 for(const readOnly of [true,undefined])expect(renderToStaticMarkup(<RunFailureNotice run={eligible} readOnly={readOnly}/>)).not.toContain('data-testid="run-correction"');
 const two={...eligible,quality:{...eligible.quality,semantic_review:{...eligible.quality.semantic_review,문제:Array(2).fill(eligible.quality.semantic_review.문제[0])}}};
 expect(renderToStaticMarkup(<RunFailureNotice run={two} readOnly={false}/>)).toContain('data-testid="run-correction"');
});
it('현재 실행은 재생 여부를 전달하고 과거 상세는 항상 읽기 전용으로 연결한다',()=>{
 expect(appSource).toContain('<RunFailureNotice run={visibleCurrent} readOnly={replaying}/>');
 expect(appSource).toContain('<RunFailureNotice run={history} readOnly/>');
});
import appSource from './앱.tsx?raw';

// 앱 테스트와 같은 no-DOM hook harness. 실제 컴포넌트의 onClick→fetch 경계를 실행한다.
const hooks=vi.hoisted(()=>({active:false,cursor:0,slots:[] as any[]}));
vi.mock('react',async original=>{
 const r=await original<typeof import('react')>();
 function state(initial:any){const i=hooks.cursor++;if(!(i in hooks.slots))hooks.slots[i]=typeof initial==='function'?initial():initial;return [hooks.slots[i],(next:any)=>{hooks.slots[i]=typeof next==='function'?next(hooks.slots[i]):next;}];}
 return {...r,useState:(initial:any)=>hooks.active?state(initial):r.useState(initial),useRef:(initial:any)=>hooks.active?state(()=>({current:initial}))[0]:r.useRef(initial)};
});
afterEach(()=>{hooks.active=false;hooks.cursor=0;hooks.slots=[];vi.unstubAllGlobals();});
function draw(run:any=eligible,readOnly=false){hooks.active=true;hooks.cursor=0;return RunFailureNotice({run,readOnly});}
function find(node:any,id:string):any{if(!node||typeof node!=='object')return; if(node.props?.['data-testid']===id)return node;for(const child of [node.props?.children].flat(Infinity)){const found=find(child,id);if(found)return found;}}
const markup=(tree:any)=>{hooks.active=false;const html=renderToStaticMarkup(tree);hooks.active=true;return html;};
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
it('수동 클릭은 pending 중 한 POST만 보내고 202는 성공 아닌 접수로 유지한다',async()=>{
 let resolve!:(value:Response)=>void;
 const fetcher=vi.fn().mockReturnValue(new Promise<Response>(yes=>{resolve=yes;}));vi.stubGlobal('fetch',fetcher);
 const tree=draw();expect(fetcher).not.toHaveBeenCalled();
 const click=find(tree,'run-correction').props.onClick;
 click();click();
 expect(fetcher).toHaveBeenCalledTimes(1);
 expect(find(draw(),'run-correction').props.disabled).toBe(true);
 resolve(new Response(JSON.stringify({run_id:'run/A',control_revision:2,source_revision:1,status:'running',max_model_calls:2}),{status:202}));await settle();
 const html=markup(draw());expect(html).toContain('보완 요청이 접수되었습니다');expect(html).toContain('통과를 뜻하지 않습니다');expect(html).not.toContain('data-testid="run-correction"');
 draw();expect(fetcher).toHaveBeenCalledTimes(1);
});


it('보완 상태는 새 revision이 running이어도 중·기시도 사유를 표시하며 다시 시작하지 않는다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 for(const status of ['pending','applied','passed','failed','completed'] as const){
  const run={...eligible,status:status==='pending'?'running':'blocked',control_revision:2,correction_state:{status,source_revision:1,max_model_calls:2}};
  const html=markup(draw(run));expect(html).toContain('data-testid="run-correction-state"');expect(html).toContain(status);expect(html).toContain('다시 시작하지 않습니다');expect(html).not.toContain('data-testid="run-correction"');
 }
 expect(fetcher).not.toHaveBeenCalled();
});
it('서버가 시작한 자동 근거 보완을 표시하고 화면 조회로 POST하지 않는다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const run={...eligible,status:'running',control_revision:2,correction_state:{status:'pending',source_revision:1,max_model_calls:2,automatic:true,mode:'evidence'}};
 const html=markup(draw(run));
 expect(html).toContain('자동 근거 보완 중입니다');
 expect(html).toContain('최대 2회');
 expect(html).toContain('원본 버전 1');
 expect(html).not.toContain('data-testid="run-correction"');
 expect(fetcher).not.toHaveBeenCalled();
});
it('자동 보완이 적용된 뒤에는 완료가 아니라 전체 재검토 중으로 표시한다',()=>{
 const run={...eligible,status:'running',control_revision:2,correction_state:{status:'applied',source_revision:1,max_model_calls:2,automatic:true,mode:'evidence'}};
 const html=markup(draw(run));
 expect(html).toContain('근거 보완을 적용하고 전체 원고를 재검토 중입니다');
 expect(html).not.toContain('통과해 제출');
 expect(html).not.toContain('data-testid="run-correction"');
});
it('자동 보완 소진 후에는 미제출과 추가 시도 금지를 함께 표시한다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const run={...eligible,control_revision:2,correction_state:{status:'failed',source_revision:1,max_model_calls:2,automatic:true,mode:'evidence'}};
 const html=markup(draw(run));
 expect(html).toContain('자동 근거 보완을 1회 시도했지만 제출하지 못했습니다');
 expect(html).toContain('다시 시작하지 않습니다');
 expect(html).not.toContain('통과해 제출');
 expect(html).not.toContain('data-testid="run-correction"');
 expect(fetcher).not.toHaveBeenCalled();
});
it('자동 보완 통과는 실제 정본과 품질 통과가 모두 있을 때만 제출로 표시한다',()=>{
 const run={...eligible,status:'completed',report_id:'report-1',control_revision:2,
  quality:{...eligible.quality,passed:true,reasons:[],semantic_review:{...eligible.quality.semantic_review,통과:true,문제:[]}},
  correction_state:{status:'completed',source_revision:1,max_model_calls:2,automatic:true,mode:'evidence'}};
 expect(markup(draw(run))).toContain('자동 근거 보완과 재검토를 통과해 제출했습니다');
 for(const changed of [{report_id:null},{status:'running'},{quality:{...run.quality,passed:false}},
  {quality:{...run.quality,semantic_review:{...run.quality.semantic_review,통과:false}}}]){
  expect(markup(draw({...run,...changed}))).not.toContain('통과해 제출');
 }
});
it('HTTP 실패 원인과 검토 지적을 보존하고 명시 재시도만 최초 UUID로 POST한다',async()=>{
 const before=JSON.stringify(eligible);
 const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({error:{code:'correction_mapping_invalid',message:'<b>고유 매핑 불가</b>'}}),{status:409})).mockResolvedValueOnce(new Response(JSON.stringify({run_id:'run/A',control_revision:2,source_revision:1,status:'running',max_model_calls:2}),{status:202}));vi.stubGlobal('fetch',fetcher);
 find(draw(),'run-correction').props.onClick();await settle();
 let tree=draw(),html=markup(tree);expect(html).toContain('correction_mapping_invalid');expect(html).toContain('&lt;b&gt;고유 매핑 불가&lt;/b&gt;');expect(html).toContain('근거 범위 초과');expect(html).not.toContain('<b>');expect(find(tree,'run-correction').props.disabled).toBe(false);
 const body=JSON.parse(fetcher.mock.calls[0][1].body);expect(body).toMatchObject({control_revision:1});expect(body.client_request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
 draw();expect(fetcher).toHaveBeenCalledTimes(1);
 find(draw(),'run-correction').props.onClick();await settle();
 expect(fetcher).toHaveBeenCalledTimes(2);expect(fetcher.mock.calls[1]).toEqual(fetcher.mock.calls[0]);expect(JSON.stringify(eligible)).toBe(before);
});
it('재생·과거 기록으로 바뀐 뒤 이전 버튼 콜백도 POST하지 않는다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const click=find(draw(),'run-correction').props.onClick;
 draw(eligible,true);click();expect(fetcher).not.toHaveBeenCalled();
 const tree=draw({...eligible,source_mode:'web'});expect(find(tree,'run-correction')).toBeUndefined();click();expect(fetcher).not.toHaveBeenCalled();
});
it('run 전환 후 이전 접수는 새 대상에 재사용하지 않는다',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({run_id:'run/A',control_revision:2,source_revision:1,status:'running',max_model_calls:2}),{status:202}));vi.stubGlobal('fetch',fetcher);
 find(draw(),'run-correction').props.onClick();await settle();
 expect(find(draw(),'run-correction')).toBeUndefined();
 expect(find(draw({...eligible,run_id:'other'}),'run-correction')).toBeDefined();
 expect(find(draw(),'run-correction')).toBeUndefined();expect(fetcher).toHaveBeenCalledTimes(1);
});

// 2026-11-07 실사용: 실행/큐는 `failed`만 보이고 사유 문구는 사건 상세를 열어야 읽혔다.
const failure={code:'model_error',message:'메모가 상한을 넘었습니다.',actor:'researcher-4'};
it('모델 의미 검토의 차단 사유와 지적 문장·출처·새 조사 비용을 바로 보인다',()=>{
 const run={status:'blocked' as const,quality:{passed:false,reasons:['semantic_review_failed'],semantic_review:{통과:false,문제:[{유형:'날짜',설명:'원문의 날짜와 다릅니다.',본문발췌:'<script>1883년</script>',출처:['임오군란']}],검토요약:'날짜 보완'}}};
 const html=renderToStaticMarkup(<RunFailureNotice run={run}/>);
 expect(html).toContain('role="alert"');expect(html).toContain('모델의 근거 검토');
 expect(html).toContain('원문의 날짜와 다릅니다');expect(html).toContain('임오군란');
 expect(html).toContain('&lt;script&gt;1883년&lt;/script&gt;');expect(html).not.toContain('<script>');
 expect(html).toContain('초안은 보존');expect(html).toContain('새 연구');expect(html).toContain('모델 호출');
});
it('의미 검토 응답 불명은 내용 오류 확정과 구별해 설명한다',()=>{
 const run={status:'blocked' as const,quality:{passed:false,reasons:['semantic_review_invalid']}};
 const html=renderToStaticMarkup(<RunFailureNotice run={run}/>);
 expect(html).toContain('검토 응답을 확인할 수 없어');expect(html).toContain('내용 오류가 확정된 것은 아닙니다');
 expect(html).toContain('자동 재시도하지 않습니다');
});
it('실패한 실행은 기록된 사유(보낸 역할·코드·문구)를 경고로 보인다',()=>{
 const html=renderToStaticMarkup(<RunFailureNotice run={{status:'failed',failure}}/>);
 expect(html).toContain('role="alert"');expect(html).toContain('data-testid="run-failure"');
 expect(html).toContain('researcher-4');expect(html).toContain('model_error');expect(html).toContain('메모가 상한을 넘었습니다.');
});
it('사유가 기록되지 않은 이전 실패는 사건 기록의 오류 항목을 안내한다',()=>{
 for(const missing of [undefined,null]){
  const html=renderToStaticMarkup(<RunFailureNotice run={{status:'failed',failure:missing}}/>);
  expect(html).toContain('실패 사유가 기록되지 않은 실행입니다');expect(html).toContain('사건 기록');
 }
});
it('실패가 아닌 실행에는 아무것도 그리지 않는다',()=>{
 for(const status of ['running','completed','blocked','cancelled','interrupted'] as const){
  expect(renderToStaticMarkup(<RunFailureNotice run={{status,failure}}/>)).toBe('');
 }
});
it('사유 문구의 마크업은 이스케이프된다',()=>{
 const html=renderToStaticMarkup(<RunFailureNotice run={{status:'failed',failure:{...failure,message:'<script>x</script>'}}}/>);
 expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');
});

it('새 문맥 검토의 근거 선택 누락에는 주장만 고치는 보완 버튼을 제공하지 않는다',()=>{
 const run={...eligible,quality:{...eligible.quality,semantic_review:{
  ...eligible.quality.semantic_review,검토계약:'source-context-review-v1',검토목록ID:'request-A',판정:'needs_revision' as const,
  문제:[{유형:'citation_gap',설명:'같은 읽은 원문 다른 문단에 근거가 있습니다.',본문발췌:'기존 주장',출처:['원문']}],
 }}};
 const html=renderToStaticMarkup(<RunFailureNotice run={run} readOnly={false}/>);
 expect(html).not.toContain('data-testid="run-correction"');
});

it('근거 선택 누락은 사실 오류가 아니라 인용 재연결이 필요한 상태로 설명한다',()=>{
 const run={...eligible,quality:{...eligible.quality,semantic_review:{
  ...eligible.quality.semantic_review,검토계약:'source-context-review-v1',판정:'needs_revision' as const,
  문제:[{유형:'citation_gap',설명:'다른 문단에 지지 내용이 있습니다.',본문발췌:'기존 주장',출처:['원문']}],
 }}};
 const html=renderToStaticMarkup(<RunFailureNotice run={run} readOnly={false}/>);
 expect(html).toContain('근거 선택 누락');
 expect(html).toContain('인용 재연결');
 expect(html).not.toContain('사실 오류가 확정');
});

it.each(['source_conflict','insufficient_context'])('새 문맥 판정 %s는 판정 불가와 내용 오류 미확정을 명시한다',type=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const run={...eligible,quality:{...eligible.quality,semantic_review:{
  ...eligible.quality.semantic_review,검토계약:'source-context-review-v1',판정:'undetermined' as const,
  문제:[{유형:type,설명:'현재 제공된 자료로 확정할 수 없습니다.',본문발췌:'기존 주장',출처:['원문']}],
 }}};
 const html=markup(draw(run));
 expect(html).toContain('판정 불가');
 expect(html).toContain('내용 오류가 확정된 것은 아닙니다');
 expect(html).not.toContain('data-testid="run-correction"');
 expect(fetcher).not.toHaveBeenCalled();
});

it('대조한 원문 문맥은 위치·출처와 함께 접어서 보존하고 열람으로 POST하지 않는다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const run={...eligible,quality:{...eligible.quality,semantic_review:{
  ...eligible.quality.semantic_review,검토계약:'source-context-review-v1',판정:'needs_revision' as const,
  문제:[{유형:'citation_gap',설명:'선택 인용 밖에 지지 기록이 있습니다.',본문발췌:'기존 주장',출처:['원문'],근거문맥:[
   {id:2,section_id:'section-1',source_id:'원문',source_hash:'a'.repeat(64),start:10,end:29,text:'원문 <script>내용</script>\n다음 줄',kind:'related' as const},
  ]}],
 }}};
 const before=JSON.stringify(run),html=markup(draw(run));
 expect(html).toContain('<details');expect(html).not.toContain('<details open');
 expect(html).toContain('대조한 원문 문맥');expect(html).toContain('문자 10–29');
 expect(html).toContain('a'.repeat(64));expect(html).toContain('원문 &lt;script&gt;내용&lt;/script&gt;');
 expect(html).not.toContain('<script>');expect(fetcher).not.toHaveBeenCalled();expect(JSON.stringify(run)).toBe(before);
});

it.each([['contradiction','사실 오류 지적 (모델 판정)'],['unsupported','제공 근거로 지지되지 않는 주장']])('새 판정의 %s를 내부 코드 대신 한글로 설명한다',(type,label)=>{
 const run={...eligible,quality:{...eligible.quality,semantic_review:{
  ...eligible.quality.semantic_review,검토계약:'source-context-review-v1',판정:'needs_revision' as const,
  문제:[{유형:type,설명:'지적 원문',본문발췌:'기존 주장',출처:['원문']}],
 }}};
 const html=renderToStaticMarkup(<RunFailureNotice run={run} readOnly={false}/>);
 expect(html).toContain(label);
 expect(html.includes('data-testid="run-correction"')).toBe(type==='contradiction');
});

it('새 판정의 자격이 바뀌면 이전 보완 콜백과 미지원 계약도 POST하지 않는다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const click=find(draw(),'run-correction').props.onClick;
 for(const contract of ['source-context-review-v1','unknown-review-version']){
  const run={...eligible,quality:{...eligible.quality,semantic_review:{
   ...eligible.quality.semantic_review,검토계약:contract,판정:'needs_revision' as const,
   문제:[{유형:'citation_gap',설명:'근거 재연결 필요',본문발췌:'기존 주장',출처:['원문']}],
  }}};
  expect(find(draw(run),'run-correction')).toBeUndefined();click();
 }
 expect(fetcher).not.toHaveBeenCalled();
});
