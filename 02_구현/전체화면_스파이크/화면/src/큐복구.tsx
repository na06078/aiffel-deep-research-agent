import {useRef,useState} from 'react';
import type {MutableRefObject} from 'react';
import type {QueueItem,RunSnapshot} from './계약';
import {api} from './통신';
import {sourceLabel} from './자료모드';

type Options={readOnly:boolean;busy:boolean;pending:MutableRefObject<boolean>;setBusy:(busy:boolean)=>void;refresh:()=>Promise<unknown>};
type Attempt={id:string;pending:boolean;accepted:boolean;error:string;notice:string};
export function useQueueRecovery(options:Options){
 const attempts=useRef(new Map<string,Attempt>());
 const latest=useRef(options);latest.current=options;
 const [,redraw]=useState(0);
 async function mutate(key:string,action:(id:string)=>Promise<unknown>,success:string){
  const current=latest.current;
  if(current.readOnly||current.busy||current.pending.current)return;
  let attempt=attempts.current.get(key);
  if(attempt?.accepted||attempt?.pending)return;
  if(!attempt){attempt={id:crypto.randomUUID(),pending:false,accepted:false,error:'',notice:''};attempts.current.set(key,attempt);}
  attempt.pending=true;attempt.error='';attempt.notice='';current.pending.current=true;current.setBusy(true);redraw(n=>n+1);
  try{
   await action(attempt.id);attempt.accepted=true;attempt.notice=success;
   try{await current.refresh();}catch(error){attempt.notice+=` 최신 상태 조회 실패: ${String(error)}. 다시 조회해 주세요. 요청을 새로 전송하지 않습니다.`;}
  }catch(error){attempt.error=`${String(error)} · 입력과 원 기록은 보존됩니다. 다시 누르면 같은 요청 ID로 접수 여부를 확인합니다.`;}
  finally{attempt.pending=false;current.pending.current=false;current.setBusy(false);redraw(n=>n+1);}
 }
 return {attempts,mutate};
}
export function QueueRecoveryControls({run,queue,readOnly,busy,recovery}:{run?:RunSnapshot;queue:QueueItem[];readOnly:boolean;busy:boolean;recovery:ReturnType<typeof useQueueRecovery>}){
 const eligible=!!run&&['failed','blocked','cancelled','interrupted'].includes(run.status);
 const retryKey=JSON.stringify(['retry',run?.run_id,run?.control_revision]);
 const locked=(key:string)=>readOnly||busy||!!recovery.attempts.current.get(key)?.pending||!!recovery.attempts.current.get(key)?.accepted;
 return <section aria-label="큐 복구">
  {run&&<><button type="button" data-testid="run-retry" disabled={locked(retryKey)||!eligible} onClick={()=>{if(!readOnly&&!busy&&eligible)void recovery.mutate(retryKey,id=>api.retryRun(run.run_id,run.control_revision,id),'새 연구 재시도가 접수되었습니다. 완료나 품질 통과를 뜻하지 않습니다. 대기 큐 정지는 유지됩니다.');}}>현재 작업 재시도</button><p>원 질문과 자료로 새 연구를 시작합니다. 원 기록과 대기 큐의 정지는 유지되며 모델 호출이 다시 발생할 수 있습니다.</p></>}
  {!readOnly&&Array.from(recovery.attempts.current.entries()).map(([key,attempt])=><div key={key}>{attempt.pending&&<p role="status">접수 확인 중입니다. 중복 요청하지 않습니다.</p>}{attempt.notice&&<p role="status">{attempt.notice}</p>}{attempt.error&&<p role="alert">{attempt.error}</p>}</div>)}
  <h3>대기 큐</h3>{queue.length?<ol>{queue.map(q=>{const key=JSON.stringify(['cancel',q.instruction_id]);return <li key={q.instruction_id}>{q.raw_text??q.text} · {sourceLabel(q.source_mode??'local')}<button type="button" data-testid={`queue-cancel-${q.instruction_id}`} disabled={locked(key)} onClick={()=>{if(!readOnly&&!busy)void recovery.mutate(key,id=>api.cancelQueued(q.instruction_id,id),'대기 지시를 취소했습니다. 다른 대기 항목과 큐 정지는 유지됩니다.');}}>대기 지시 취소</button></li>;})}</ol>:<p>대기 중인 지시가 없습니다.</p>}
 </section>;
}
