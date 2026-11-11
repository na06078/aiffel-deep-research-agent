import {it,expect,vi,afterEach} from 'vitest';
import {api} from './통신';
import {renderToStaticMarkup} from 'react-dom/server';
import {QueueRecoveryControls,useQueueRecovery} from './큐복구';
const hooks=vi.hoisted(()=>({active:false,cursor:0,slots:[] as any[]}));
vi.mock('react',async original=>{
 const r=await original<typeof import('react')>();
 function state(initial:any){const i=hooks.cursor++;if(!(i in hooks.slots))hooks.slots[i]=typeof initial==='function'?initial():initial;return [hooks.slots[i],(next:any)=>{hooks.slots[i]=typeof next==='function'?next(hooks.slots[i]):next;}];}
 return {...r,useState:(initial:any)=>hooks.active?state(initial):r.useState(initial),useRef:(initial:any)=>hooks.active?state(()=>({current:initial}))[0]:r.useRef(initial)};
});
afterEach(()=>{hooks.active=false;hooks.cursor=0;hooks.slots=[];});
const pending={current:false};
const refresh=vi.fn(async()=>{});
const run={run_id:'old/A',control_revision:2,status:'failed',question:'원 질문'} as any;
const queue=[{instruction_id:'q1',text:'대기 질문',status:'queued' as const}];
function draw(readOnly=false,busy=false,status='failed'){
 hooks.active=true;hooks.cursor=0;
 const recovery=useQueueRecovery({readOnly,busy,pending,setBusy:()=>{},refresh});
 return QueueRecoveryControls({run:{...run,status} as any,queue,readOnly,busy,recovery});
}
function find(node:any,id:string):any{if(!node||typeof node!=='object')return;if(node.props?.['data-testid']===id)return node;for(const child of [node.props?.children].flat(Infinity)){const found=find(child,id);if(found)return found;}}
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
it('현재 실패 재시도와 각 대기 지시 취소를 실제 버튼으로 제공한다',()=>{
 const tree=draw();
 expect(find(tree,'run-retry')).toBeTruthy();
 expect(find(tree,'queue-cancel-q1')).toBeTruthy();
 hooks.active=false;const html=renderToStaticMarkup(tree);expect(html).toContain('현재 작업 재시도');expect(html).toContain('대기 지시 취소');
});
it('명시 클릭만 POST하며 처리 중 중복을 막고 알 수 없는 결과는 같은 ID로 확인한다',async()=>{
 const fetcher=vi.fn().mockRejectedValueOnce(new Error('network lost')).mockResolvedValueOnce(new Response('{"status":"received"}',{status:202}));vi.stubGlobal('fetch',fetcher);
 const tree=draw();expect(fetcher).not.toHaveBeenCalled();
 find(tree,'run-retry').props.onClick?.();await settle();
 expect(fetcher).toHaveBeenCalledTimes(1);
 const first=JSON.parse(fetcher.mock.calls[0][1].body);
 hooks.active=false;expect(renderToStaticMarkup(draw())).toContain('network lost');
 find(draw(),'run-retry').props.onClick?.();await settle();
 expect(fetcher).toHaveBeenCalledTimes(2);
 expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(first);
 hooks.active=false;expect(renderToStaticMarkup(draw())).toContain('접수되었습니다');
 expect(find(draw(),'run-retry').props.disabled).toBe(true);
});
it('큐 취소는 중복 클릭 중 한 번만 전송하고 완료 뒤 재전송하지 않는다',async()=>{
 let resolve!:(value:Response)=>void;
 const fetcher=vi.fn().mockReturnValue(new Promise<Response>(yes=>{resolve=yes;}));vi.stubGlobal('fetch',fetcher);
 const click=find(draw(),'queue-cancel-q1').props.onClick;click();click();
 expect(fetcher).toHaveBeenCalledTimes(1);expect(find(draw(),'queue-cancel-q1').props.disabled).toBe(true);
 resolve(new Response('{"instruction_id":"q1","status":"cancelled"}'));await settle();
 expect(find(draw(),'queue-cancel-q1').props.disabled).toBe(true);
 find(draw(),'queue-cancel-q1').props.onClick();await settle();expect(fetcher).toHaveBeenCalledTimes(1);
});
it('재생과 공통 busy 및 진행·완료 실행에서 mutation은 버튼과 핸들러 양쪽으로 차단한다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 for(const [readOnly,busy] of [[true,false],[false,true]]){
  const tree=draw(readOnly,busy);for(const id of ['run-retry','queue-cancel-q1']){const button=find(tree,id);expect(button.props.disabled).toBe(true);button.props.onClick();}
 }
 for(const status of ['running','completed']){const button=find(draw(false,false,status),'run-retry');expect(button.props.disabled).toBe(true);button.props.onClick();}
 expect(fetcher).not.toHaveBeenCalled();
});
it('실시간 때의 오래된 클릭도 재생으로 전환한 뒤에는 POST하지 않는다',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const tree=draw();const click=find(tree,'run-retry').props.onClick;draw(true);click();expect(fetcher).not.toHaveBeenCalled();
});
it('접수 뒤 조회만 실패하면 새 mutation 없이 성공과 조회 실패를 구분한다',async()=>{
 refresh.mockRejectedValueOnce(new Error('read failed'));
 const fetcher=vi.fn().mockResolvedValue(new Response('{"status":"received"}',{status:202}));vi.stubGlobal('fetch',fetcher);
 find(draw(),'run-retry').props.onClick();await settle();
 const tree=draw();hooks.active=false;const html=renderToStaticMarkup(tree);expect(html).toContain('접수되었습니다');expect(html).toContain('최신 상태 조회 실패');
 expect(find(draw(),'run-retry').props.disabled).toBe(true);expect(fetcher).toHaveBeenCalledTimes(1);
});
afterEach(()=>{vi.unstubAllGlobals();pending.current=false;refresh.mockReset();});
it('대기 취소의 알 수 없는 결과도 항목별 원 요청 ID를 보존하고 오류를 숨기지 않는다',async()=>{
 const fetcher=vi.fn().mockRejectedValueOnce(new Error('unknown cancellation')).mockResolvedValueOnce(new Response('{"instruction_id":"q1","status":"cancelled"}'));vi.stubGlobal('fetch',fetcher);
 find(draw(),'queue-cancel-q1').props.onClick();await settle();const original=fetcher.mock.calls[0][1].body;
 const tree=draw();hooks.active=false;expect(renderToStaticMarkup(tree)).toContain('unknown cancellation');
 find(draw(),'queue-cancel-q1').props.onClick();await settle();
 expect(fetcher.mock.calls[1][1].body).toBe(original);expect(fetcher).toHaveBeenCalledTimes(2);
});
it('현재 재시도는 원 버전과 고정 ID만 POST한다',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({status:'received',run_id:'new'}),{status:202}));vi.stubGlobal('fetch',fetcher);
 expect(typeof (api as any).retryRun).toBe('function');
 await (api as any).retryRun('A/B',3,'fixed');
 expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/spike/runs/A%2FB/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({control_revision:3,client_request_id:'fixed'})});
});
it('대기 취소는 정확한 ID와 요청 ID만 POST한다',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response('{}'));vi.stubGlobal('fetch',fetcher);
 expect(typeof (api as any).cancelQueued).toBe('function');
 await (api as any).cancelQueued('Q/B','cancel-fixed');
 expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/spike/queue/Q%2FB/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_request_id:'cancel-fixed'})});
});
