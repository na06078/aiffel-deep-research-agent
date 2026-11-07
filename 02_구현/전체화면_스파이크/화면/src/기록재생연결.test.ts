import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import type {Snapshot,RunSnapshot,RunDeleteResult} from './계약';
import type {RunArchive} from './시간계약';
import {api} from './통신';
import {useRecordedReplay} from './기록재생연결';
import {useRunDelete} from './실행삭제.tsx';

// No DOM package is installed. Execute both production hooks with committed effects,
// dependency comparisons, cleanup and state-driven rerenders (not SSR/no-op effects).
const hooks=vi.hoisted(()=>({cursor:0,slots:[] as any[],pending:[] as (()=>void)[],dirty:false}));
vi.mock('react',async original=>{
 const react=await original<typeof import('react')>();
 function state(initial:any){
  const i=hooks.cursor++;
  if(!(i in hooks.slots))hooks.slots[i]=typeof initial==='function'?initial():initial;
  return [hooks.slots[i],(next:any)=>{const value=typeof next==='function'?next(hooks.slots[i]):next;if(!Object.is(value,hooks.slots[i])){hooks.slots[i]=value;hooks.dirty=true;}}];
 }
 return {...react,useState:state,useRef:(initial:any)=>state(()=>({current:initial}))[0],
  useReducer:(reducer:any,initial:any)=>{const [value,set]=state(initial);return [value,(action:any)=>set((old:any)=>reducer(old,action))];},
  useMemo:(fn:any)=>fn(),
  useEffect:(effect:()=>void|(()=>void),deps?:unknown[])=>{
   const i=hooks.cursor++,old=hooks.slots[i];
   if(!old||!deps||deps.some((dep,j)=>!Object.is(dep,old.deps?.[j]))||deps.length!==old.deps?.length){
    hooks.pending.push(()=>{old?.cleanup?.();hooks.slots[i]={deps,cleanup:effect()};});
   }
  }};
});
const counts={events:0,calls:0,activity_spans:0,requests:0,reports:0,report_files:0,export_files:0,web_sources:0,web_reads:0};
const run=(id:string):RunSnapshot=>({run_id:id,question:id,status:'completed',control_revision:1,phase:'submitted',plan:[],draft:null,report_id:null,quality:{passed:null,reasons:[]},parent_report_id:null,created_at:'2026-11-08T00:00:00Z',updated_at:'2026-11-08T00:00:01Z',mock_requests:0,actual_model_requests:0});
const snapshot=(current:string|null,ids=['A','B','C'],seq=10):Snapshot=>({protocol_version:1,provider:'test-double',mode:'spike',runs:ids.map(run),events:[],waiting:[],queue:[],current_run_id:current,queue_paused:false,queue_pause_reason:null,high_watermark_seq:seq,events_has_more:false,mock_requests:0,actual_model_requests:0});
const archive=(id:string):RunArchive=>({protocol_version:1,provider:'test-double',run:run(id),revisions:[run(id)],events:[],spans:[],duration_ms:1000,started_at:run(id).created_at,ended_at:run(id).updated_at,recorded_at:run(id).updated_at,high_watermark_seq:10,has_measured_spans:false,truncated:false});
function deferred<T>(){let resolve!:(value:T)=>void,reject!:(error:Error)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
let current:Snapshot|null,playback:ReturnType<typeof useRecordedReplay>,deletion:ReturnType<typeof useRunDelete>;
let post:ReturnType<typeof deferred<RunDeleteResult>>,refresh:ReturnType<typeof vi.fn<()=>Promise<unknown>>>,storage:Map<string,string>;
function render(){
 hooks.cursor=0;hooks.dirty=false;
 playback=useRecordedReplay(current);
 // Same callbacks as App; the delete response and journal snapshot are independent.
 deletion=useRunDelete({replayRunId:playback.replay?playback.runId:null,popup:null,refresh,leaveReplay:playback.live,
  onDeletedRun:playback.deleted,returnToList:vi.fn(),notify:vi.fn()});
 const effects=hooks.pending.splice(0);effects.forEach(effect=>effect());
}
function flush(){for(let i=0;i<30;i++){render();if(!hooks.dirty)return;}throw new Error('hook rerender did not settle');}
async function settle(){for(let i=0;i<8;i++){await Promise.resolve();flush();}}
async function startDelete(id='C'){
 deletion.start(id);await settle();expect(deletion.flow.phase).toBe('ready');
 const done=deletion.confirm();flush();return {done};
}
function success(id='C',next:string|null='A'){post.resolve({status:'deleted',run_id:id,counts,warnings:[],current_run_id:next});}
beforeEach(()=>{
 hooks.cursor=0;hooks.slots=[];hooks.pending=[];hooks.dirty=false;vi.useFakeTimers();
 storage=new Map([['village-record-replay',JSON.stringify({runId:'C',at:500})]]);
 vi.stubGlobal('sessionStorage',{getItem:(key:string)=>storage.get(key)??null,setItem:(key:string,value:string)=>storage.set(key,value),removeItem:(key:string)=>storage.delete(key)});
 current=snapshot('C');post=deferred<RunDeleteResult>();refresh=vi.fn(()=>new Promise(()=>{}));
 vi.spyOn(api,'archive').mockImplementation(async id=>archive(id));
 vi.spyOn(api,'deletePreview').mockImplementation(async id=>({run_id:id,deletable:true,blockers:[],counts}));
 vi.spyOn(api,'deleteRun').mockImplementation(()=>post.promise);
});
afterEach(()=>{hooks.slots.forEach(slot=>slot?.cleanup?.());vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers();});

it('삭제 성공이 snapshot 갱신보다 먼저여도 오래된 current C의 archive를 새로 조회하지 않는다',async()=>{
 await settle();expect(playback.replay).toBe(true);expect(playback.archive?.run.run_id).toBe('C');
 expect(api.archive).toHaveBeenCalledExactlyOnceWith('C');
 await startDelete();success();await settle();
 expect(deletion.flow.phase).toBe('done');expect(refresh).toHaveBeenCalledOnce();
 expect(playback.replay).toBe(false);
 expect(api.archive).toHaveBeenCalledExactlyOnceWith('C');
 expect(playback.archive).toBeNull();expect(playback.projection).toBeNull();
 expect(storage.has('village-record-replay')).toBe(false);
 current=snapshot('A',['A','B'],11);await settle();
 expect(api.archive).toHaveBeenLastCalledWith('A');expect(playback.archive?.run.run_id).toBe('A');
});

it('SSE snapshot에서 재생 대상이 먼저 사라지면 첫 렌더부터 과거 투영을 숨기고 늦은 응답을 폐기한다',async()=>{
 await settle();expect(playback.projection?.run.run_id).toBe('C');
 // A pending retry must not resurrect C after the independent snapshot removes it.
 const late=deferred<RunArchive>();vi.mocked(api.archive).mockReturnValueOnce(late.promise);
 playback.retry();flush();await startDelete();
 current=snapshot('A',['A','B'],11);render();
 expect(playback.projection).toBeNull();expect(playback.replay).toBe(false);
 await settle();expect(playback.archive?.run.run_id).toBe('A');
 expect(storage.has('village-record-replay')).toBe(false);
 late.resolve(archive('C'));await settle();
 expect(playback.archive?.run.run_id).toBe('A');
 success();await settle();expect(playback.replay).toBe(false);
 expect(vi.mocked(api.archive).mock.calls.map(([id])=>id)).toEqual(['C','C','A']);
});

it.each([null,'A'] as const)('삭제 응답 후 최신 current=%s를 독립적으로 받아들이며 늦은 archive 성공은 되살리지 않는다',async next=>{
 const late=deferred<RunArchive>();vi.mocked(api.archive).mockReturnValueOnce(late.promise);
 await settle();expect(playback.loading).toBe(true);
 await startDelete();success('C',next);await settle();
 expect(api.archive).toHaveBeenCalledExactlyOnceWith('C');
 expect(playback.replay).toBe(false);expect(playback.loading).toBe(false);
 current=snapshot(next,next?['A','B']:[],11);await settle();
 late.resolve(archive('C'));await settle();
 expect(playback.archive?.run.run_id??null).toBe(next);expect(playback.error).toBe('');
 expect(storage.has('village-record-replay')).toBe(false);
});

it('실시간 current C 삭제 후 오래된 snapshot의 watermark가 변해도 C를 조회하지 않는다',async()=>{
 storage.clear();await settle();expect(playback.replay).toBe(false);
 await startDelete();success();await settle();
 current=snapshot('C',['A','B','C'],11);await settle();playback.retry();await settle();
 expect(api.archive).toHaveBeenCalledExactlyOnceWith('C');expect(playback.archive).toBeNull();
 current=snapshot('A',['A','B'],12);await settle();
 expect(playback.archive?.run.run_id).toBe('A');
});

it('B를 삭제해도 C의 재생·cursor·sessionStorage와 진행 중 응답을 유지한다',async()=>{
 const late=deferred<RunArchive>();vi.mocked(api.archive).mockReturnValueOnce(late.promise);
 await settle();await startDelete('B');success('B','C');await settle();
 current=snapshot('C',['A','C'],11);await settle();late.resolve(archive('C'));await settle();
 expect(playback.replay).toBe(true);expect(playback.runId).toBe('C');expect(playback.at).toBe(500);
 expect(playback.archive?.run.run_id).toBe('C');expect(api.archive).toHaveBeenCalledExactlyOnceWith('C');
 expect(JSON.parse(storage.get('village-record-replay')!).runId).toBe('C');
});

it('삭제를 기다리며 C에서 A로 재생을 바꿨다면 C 성공 응답이 A를 해제하지 않는다',async()=>{
 await settle();await startDelete();playback.select('A');await settle();success();await settle();
 expect(playback.runId).toBe('A');expect(playback.replay).toBe(true);expect(playback.archive?.run.run_id).toBe('A');
});

it('최초 snapshot=null에서 저장된 재생을 복원하고 첫 목록에 없는 미확인 ID도 임의 해제하지 않는다',async()=>{
 current=null;await settle();expect(playback.runId).toBe('C');expect(playback.archive?.run.run_id).toBe('C');
 current=snapshot('A',['A'],11);await settle();
 expect(playback.runId).toBe('C');expect(playback.replay).toBe(true);expect(api.archive).toHaveBeenCalledExactlyOnceWith('C');
 expect(storage.has('village-record-replay')).toBe(true);
});

it('수동 미확인 ID의 404와 네트워크 오류는 표시하고 같은 대상으로 재시도한다',async()=>{
 await settle();vi.mocked(api.archive).mockRejectedValueOnce(new Error('not_found: HTTP 404'));
 playback.select('unknown');await settle();
 expect(playback.runId).toBe('unknown');expect(playback.replay).toBe(true);expect(playback.error).toContain('HTTP 404');
 vi.mocked(api.archive).mockRejectedValueOnce(new Error('network offline'));
 playback.retry();await settle();expect(playback.error).toContain('network offline');
 playback.retry();await settle();expect(playback.error).toBe('');expect(playback.archive?.run.run_id).toBe('unknown');
 expect(vi.mocked(api.archive).mock.calls.map(([id])=>id)).toEqual(['C','unknown','unknown','unknown']);
});

it('live watermark 갱신은 계속 archive를 갱신하고 선택 재생의 seek/play는 추가 GET을 만들지 않는다',async()=>{
 storage.clear();await settle();current=snapshot('C',['A','B','C'],11);await settle();
 expect(vi.mocked(api.archive).mock.calls.map(([id])=>id)).toEqual(['C','C']);
 playback.seek(300);await settle();const requests=vi.mocked(api.archive).mock.calls.length;
 playback.seek(200);await settle();playback.play();await settle();
 vi.advanceTimersByTime(50);await settle();expect(playback.playing).toBe(true);expect(playback.replay).toBe(true);
 expect(api.archive).toHaveBeenCalledTimes(requests);
});

it('삭제 뒤 도착하는 과거 archive 오류도 현재 대상의 오류로 표시하지 않는다',async()=>{
 const late=deferred<RunArchive>();vi.mocked(api.archive).mockReturnValueOnce(late.promise);
 await settle();await startDelete();success();await settle();
 current=snapshot('A',['A','B'],11);await settle();late.reject(new Error('obsolete HTTP 404'));await settle();
 expect(playback.error).toBe('');expect(playback.archive?.run.run_id).toBe('A');expect(playback.loading).toBe(false);
});

it('삭제 실패는 재생을 유지하며 재조회·동일 요청 ID 재시도를 막지 않는다',async()=>{
 await settle();await startDelete();post.reject(new Error('HTTP 500'));await settle();
 expect(deletion.flow.phase).toBe('error');expect(playback.runId).toBe('C');expect(refresh).not.toHaveBeenCalled();
 playback.retry();await settle();expect(api.archive).toHaveBeenCalledTimes(2);
 const requestId=vi.mocked(api.deleteRun).mock.calls[0][1];post=deferred<RunDeleteResult>();
 void deletion.confirm();flush();success();await settle();
 expect(vi.mocked(api.deleteRun).mock.calls[1][1]).toBe(requestId);expect(playback.replay).toBe(false);
});

it('snapshot만으로 마지막 재생 실행이 사라져도 빈 실시간과 저장 키 정리를 유지한다',async()=>{
 await settle();current=snapshot(null,[],11);render();
 expect(playback.projection).toBeNull();expect(playback.replay).toBe(false);expect(playback.archive).toBeNull();
 await settle();expect(storage.has('village-record-replay')).toBe(false);expect(api.archive).toHaveBeenCalledExactlyOnceWith('C');
});
