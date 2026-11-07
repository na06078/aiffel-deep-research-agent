// 실행 삭제 확인 화면과 훅 (SPEC 28.5). 판단 로직은 실행삭제.ts에 있고 여기는 연결만 한다.
import {useReducer,useRef} from 'react';
import type {RunDeleteBlocker,RunSnapshot} from './계약';
import {api} from './통신';
import type {Popup} from './창상태';
import {
  deleteNotice,deleteReducer,describeDeleteError,idleFlow,popupAfterDelete,scopeText,shouldLeaveReplay,
} from './실행삭제.ts';
import type {DeleteFlow} from './실행삭제.ts';

const runningTitle='진행 중인 실행은 삭제할 수 없습니다. 먼저 취소하세요.';
const visibleDependents=3;

// 진행 중인 실행 자신만 막는다. 다른 실행은 서버가 정본으로 다시 판정한다.
export function DeleteButton({run,onStart}:{run:Pick<RunSnapshot,'run_id'|'status'>;onStart:(runId:string)=>void}){
  const running=run.status==='running';
  return (
    <button
      className="danger"
      data-testid="history-delete"
      disabled={running}
      title={running?runningTitle:undefined}
      onClick={()=>onStart(run.run_id)}
    >삭제</button>
  );
}

function BlockerItem({blocker}:{blocker:RunDeleteBlocker}){
  const dependents=blocker.dependents??[];
  const hidden=dependents.length-visibleDependents;
  return (
    <li>
      {blocker.message}
      {dependents.length>0&&(
        <ul className="delete-dependents">
          {dependents.slice(0,visibleDependents).map(dep=><li key={dep.run_id}>{dep.question}</li>)}
          {hidden>0&&<li>{`외 ${hidden}건`}</li>}
        </ul>
      )}
    </li>
  );
}

interface DeleteConfirmProps{
  flow:DeleteFlow;
  onConfirm:()=>void;
  onCancel:()=>void;
}

// 구성요소가 아니라 요소를 돌려주는 함수다(테스트·콜백 탐색이 button을 바로 찾도록).
function cancelButton(onCancel:()=>void,disabled=false){
  return <button data-testid="history-delete-cancel" disabled={disabled} onClick={onCancel}>취소</button>;
}

export function DeleteConfirm({flow,onConfirm,onCancel}:DeleteConfirmProps){
  if(flow.phase==='idle'||flow.phase==='done')return null;
  const group=(children:React.ReactNode)=>(
    <div className="delete-confirm" data-testid="history-delete-confirm" role="group" aria-label="삭제 확인">{children}</div>
  );
  if(flow.phase==='loading')return group(<p role="status">삭제 범위를 확인하는 중입니다.</p>);
  if(flow.phase==='blocked'){
    return group(<>
      <ul className="delete-blocked" data-testid="history-delete-blocked">
        {flow.preview.blockers.length
          ?flow.preview.blockers.map(blocker=><BlockerItem key={blocker.code} blocker={blocker}/>)
          :<li>삭제할 수 없는 실행입니다.</li>}
      </ul>
      <div className="delete-actions">{cancelButton(onCancel)}</div>
    </>);
  }
  // 조회 자체가 실패했으면(preview 없음) 범위를 모르므로 삭제 확인을 내지 않는다.
  if(flow.phase==='error'&&!flow.preview){
    return group(<>
      <p role="alert">{flow.message}</p>
      <div className="delete-actions">{cancelButton(onCancel)}</div>
    </>);
  }
  const busy=flow.phase==='busy';
  return group(<>
    <p className="delete-scope">{`${scopeText(flow.preview!.counts)}을 영구 삭제합니다.`}</p>
    <p className="delete-note">호출 예산 기록은 유지됩니다. 되돌릴 수 없습니다.</p>
    {busy&&<p role="status">삭제하는 중입니다.</p>}
    {flow.phase==='error'&&<p role="alert">{flow.message}</p>}
    <div className="delete-actions">
      <button className="danger" data-testid="history-delete-yes" disabled={busy} onClick={onConfirm}>삭제 확인</button>
      {cancelButton(onCancel,busy)}
    </div>
  </>);
}

export interface RunDeleteDeps{
  replayRunId:string|null;
  popup:Popup|null;
  refresh:()=>Promise<unknown>|void;
  leaveReplay:()=>void;
  onDeletedRun?:(runId:string)=>void;
  returnToList:()=>void;
  notify:(text:string)=>void;
}

// 상태 기계를 API와 앱 콜백에 연결한다. 앱의 다른 상태는 콜백으로만 건드린다.
export function useRunDelete(deps:RunDeleteDeps){
  const [flow,dispatch]=useReducer(deleteReducer,idleFlow);
  // 비동기 응답이 도착했을 때의 최신 재생·창 상태를 읽기 위해 ref에 둔다.
  const latest=useRef(deps);
  latest.current=deps;

  function start(runId:string){
    // 열 때마다 새 요청 ID. 같은 확인 안의 재시도는 이 값을 그대로 쓴다.
    const requestId=crypto.randomUUID();
    dispatch({type:'open',runId,requestId});
    api.deletePreview(runId)
      .then(preview=>dispatch({type:'previewLoaded',requestId,preview}))
      .catch(error=>dispatch({type:'previewFailed',requestId,message:describeDeleteError(error)}));
  }

  async function afterSuccess(result:Parameters<typeof deleteNotice>[0]){
    const current=latest.current;
    current.onDeletedRun?.(result.run_id);
    if(shouldLeaveReplay(current.replayRunId,result.run_id))current.leaveReplay();
    if(popupAfterDelete(current.popup))current.returnToList();
    current.notify(deleteNotice(result));
    // 삭제는 이미 끝났으므로 스냅샷 갱신 실패가 삭제 실패로 보이면 안 된다(SSE가 곧 따라잡는다).
    try{await current.refresh();}catch{/* 다음 폴링이 갱신한다 */}
  }

  async function confirm(){
    if(flow.phase!=='ready'&&!(flow.phase==='error'&&flow.preview))return;
    const {runId,requestId}=flow;
    dispatch({type:'confirm'});
    try{
      const result=await api.deleteRun(runId,requestId);
      dispatch({type:'succeeded',requestId,result});
      await afterSuccess(result);
    }catch(error){
      dispatch({type:'failed',requestId,message:describeDeleteError(error)});
    }
  }

  return {flow,start,confirm,cancel:()=>dispatch({type:'cancel'})};
}
