// 실행 삭제 확인 흐름의 순수 로직 (SPEC 28.5). React·네트워크에 의존하지 않는다.
import type {JournalEvent,RunDeleteCounts,RunDeletePreview,RunDeleteResult} from './계약';
import type {Popup} from './창상태';

export type DeleteFlow =
  | {phase:'idle'}
  | {phase:'loading';runId:string;requestId:string}
  | {phase:'blocked';runId:string;requestId:string;preview:RunDeletePreview}
  | {phase:'ready';runId:string;requestId:string;preview:RunDeletePreview}
  | {phase:'busy';runId:string;requestId:string;preview:RunDeletePreview}
  // preview가 null이면 미리보기 조회 자체가 실패한 것이라 삭제 확인으로 갈 수 없다.
  | {phase:'error';runId:string;requestId:string;preview:RunDeletePreview|null;message:string}
  | {phase:'done';runId:string;requestId:string;result:RunDeleteResult};

// requestId는 두 역할을 한다.
// 1) 서버 멱등 키(client_request_id): 오류 뒤 재시도에서도 바뀌지 않아야 중복 삭제가 안전하다.
// 2) 늦게 도착한 응답을 가려내는 표식: 다시 열 때마다 새 값이라 이전 확인의 응답이 섞이지 않는다.
export type DeleteAction =
  | {type:'open';runId:string;requestId:string}
  | {type:'previewLoaded';requestId:string;preview:RunDeletePreview}
  | {type:'previewFailed';requestId:string;message:string}
  | {type:'confirm'}
  | {type:'succeeded';requestId:string;result:RunDeleteResult}
  | {type:'failed';requestId:string;message:string}
  | {type:'cancel'};

export const idleFlow:DeleteFlow={phase:'idle'};

export function deleteReducer(state:DeleteFlow,action:DeleteAction):DeleteFlow{
  switch(action.type){
    case 'open':
      // 삭제 요청이 서버에 가 있는 동안은 다른 확인을 열지 않는다(결과를 잃지 않기 위해).
      if(state.phase==='busy')return state;
      return {phase:'loading',runId:action.runId,requestId:action.requestId};
    case 'previewLoaded':
      if(state.phase!=='loading'||state.requestId!==action.requestId)return state;
      return {
        phase:action.preview.deletable?'ready':'blocked',
        runId:state.runId,requestId:state.requestId,preview:action.preview,
      };
    case 'previewFailed':
      if(state.phase!=='loading'||state.requestId!==action.requestId)return state;
      return {phase:'error',runId:state.runId,requestId:state.requestId,preview:null,message:action.message};
    case 'confirm':
      // ready와 (미리보기가 있는) error에서만 진행한다. 같은 requestId를 그대로 유지한다.
      if(state.phase==='ready'||(state.phase==='error'&&state.preview)){
        return {phase:'busy',runId:state.runId,requestId:state.requestId,preview:state.preview!};
      }
      return state;
    case 'succeeded':
      if(state.phase!=='busy'||state.requestId!==action.requestId)return state;
      return {phase:'done',runId:state.runId,requestId:state.requestId,result:action.result};
    case 'failed':
      if(state.phase!=='busy'||state.requestId!==action.requestId)return state;
      return {phase:'error',runId:state.runId,requestId:state.requestId,preview:state.preview,message:action.message};
    case 'cancel':
      return state.phase==='busy'?state:idleFlow;
  }
}

// 이 실행의 카드(또는 상세)에 보여 줄 확인 상태. 닫혔거나 다른 실행이면 null.
export function armedFlowFor(flow:DeleteFlow,runId:string):DeleteFlow|null{
  if(flow.phase==='idle'||flow.phase==='done')return null;
  return flow.runId===runId?flow:null;
}

const errorMessages:Record<string,string>={
  run_running:'진행 중인 실행은 삭제할 수 없습니다. 먼저 취소하세요.',
  has_dependents:'이 보고서를 문맥으로 쓴 실행이 있어 삭제할 수 없습니다. 먼저 그 실행을 삭제하세요.',
  confirm_mismatch:'삭제 대상이 일치하지 않습니다. 확인을 닫고 다시 시도하세요.',
  not_found:'이미 삭제되었거나 찾을 수 없는 실행입니다.',
};

// request()는 `code: message`로 던지므로 앞쪽 코드로 한국어 문구를 고른다.
// 모르는 오류는 원문을 그대로 보여 숨기지 않는다.
export function describeDeleteError(error:unknown):string{
  const raw=(error instanceof Error?error.message:String(error)).replace(/^Error:\s*/,'');
  const code=raw.split(':')[0].trim();
  return errorMessages[code]??raw;
}

export function scopeText(counts:RunDeleteCounts):string{
  return `사건 ${counts.events}건 · 보고서 ${counts.reports}건 · 변환 파일 ${counts.export_files}개 · 웹 자료 ${counts.web_sources}건`;
}

export function deleteNotice(result:RunDeleteResult):string{
  return ['실행을 삭제했습니다.',scopeText(result.counts),...result.warnings].join(' ');
}

// 재생 중인 실행이 지워졌을 때만 실시간으로 돌아간다. 다른 실행의 재생은 건드리지 않는다.
export function shouldLeaveReplay(replayRunId:string|null,deletedRunId:string):boolean{
  return replayRunId!==null&&replayRunId===deletedRunId;
}

// 사건·보고서·실행 상세 창은 지워진 기록을 가리킬 수 있으므로 과거 기록 목록으로 돌린다.
// null은 "창을 바꾸지 않는다"는 뜻이다.
export function popupAfterDelete(popup:Popup|null):Popup|null{
  if(!popup)return null;
  return popup.kind==='event'||popup.kind==='report'||popup.kind==='history'?{kind:'과거 기록'}:null;
}

// 삭제 흔적 사건은 서버에 남기되 화면의 어떤 사건 목록에도 보이지 않는다.
export function visibleEvents<T extends Pick<JournalEvent,'type'>>(events:readonly T[]):T[]{
  return events.filter(event=>event.type!=='run.deleted');
}
