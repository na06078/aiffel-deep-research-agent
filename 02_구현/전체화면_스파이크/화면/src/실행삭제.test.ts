import {describe,expect,it} from 'vitest';
import type {RunDeletePreview,RunDeleteResult} from './계약';
import type {JournalEvent} from './계약';
import {
  armedFlowFor,deleteNotice,deleteReducer,describeDeleteError,idleFlow,popupAfterDelete,
  scopeText,shouldLeaveReplay,visibleEvents,
} from './실행삭제.ts';
import type {DeleteFlow} from './실행삭제.ts';

const counts={events:12,calls:4,activity_spans:6,requests:3,reports:1,report_files:1,export_files:2,web_sources:5,web_reads:7};
const readyPreview:RunDeletePreview={run_id:'run-A',deletable:true,blockers:[],counts};
const blockedPreview:RunDeletePreview={
  run_id:'run-A',deletable:false,counts,
  blockers:[{code:'has_dependents',message:'후속 실행이 있습니다.',dependents:[{run_id:'run-B',question:'후속 질문',created_at:'2026-11-08T00:00:00Z'}]}],
};
const result:RunDeleteResult={status:'deleted',run_id:'run-A',counts,warnings:[],current_run_id:null};

const open=(runId='run-A',requestId='req-1')=>deleteReducer(idleFlow,{type:'open',runId,requestId});
const ready=()=>deleteReducer(open(),{type:'previewLoaded',requestId:'req-1',preview:readyPreview});
const busy=()=>deleteReducer(ready(),{type:'confirm'});
const failed=(message='서버 오류')=>deleteReducer(busy(),{type:'failed',requestId:'req-1',message});

describe('삭제 확인 상태 기계',()=>{
  it('idle에서 열면 조회 중이 되고 요청 ID를 이 시점에 고정한다',()=>{
    expect(open()).toEqual({phase:'loading',runId:'run-A',requestId:'req-1'});
  });

  it('조회 결과가 deletable이면 ready, 아니면 blocked(확인 단계 없음)',()=>{
    expect(ready().phase).toBe('ready');
    const blocked=deleteReducer(open(),{type:'previewLoaded',requestId:'req-1',preview:blockedPreview});
    expect(blocked.phase).toBe('blocked');
    // 막힘에서는 삭제 확인으로 갈 수 없다.
    expect(deleteReducer(blocked,{type:'confirm'})).toBe(blocked);
  });

  it('조회 실패는 미리보기 없는 error이고 삭제 확인으로 갈 수 없다',()=>{
    const state=deleteReducer(open(),{type:'previewFailed',requestId:'req-1',message:'조회 실패'});
    expect(state).toMatchObject({phase:'error',preview:null,message:'조회 실패'});
    expect(deleteReducer(state,{type:'confirm'})).toBe(state);
  });

  it('ready에서 확인하면 busy, 성공하면 done',()=>{
    expect(busy().phase).toBe('busy');
    const done=deleteReducer(busy(),{type:'succeeded',requestId:'req-1',result});
    expect(done).toMatchObject({phase:'done',runId:'run-A',result});
  });

  it('오류 뒤 다시 확인해도 같은 클라이언트 요청 ID를 쓴다(멱등 재시도)',()=>{
    const error=failed();
    expect(error).toMatchObject({phase:'error',requestId:'req-1',message:'서버 오류'});
    const retry=deleteReducer(error,{type:'confirm'});
    expect(retry).toMatchObject({phase:'busy',requestId:'req-1'});
  });

  it('한 번에 하나만 무장한다: 다른 실행을 열면 앞의 확인은 사라진다',()=>{
    const second=deleteReducer(ready(),{type:'open',runId:'run-B',requestId:'req-2'});
    expect(second).toEqual({phase:'loading',runId:'run-B',requestId:'req-2'});
    expect(armedFlowFor(second,'run-A')).toBeNull();
    expect(armedFlowFor(second,'run-B')).toBe(second);
  });

  it('삭제 중에는 다른 실행을 열거나 취소할 수 없다',()=>{
    const state=busy();
    expect(deleteReducer(state,{type:'open',runId:'run-B',requestId:'req-2'})).toBe(state);
    expect(deleteReducer(state,{type:'cancel'})).toBe(state);
  });

  it('취소하면 idle로 돌아가고 낡은 응답은 무시한다',()=>{
    const loading=open();
    const cancelled=deleteReducer(loading,{type:'cancel'});
    expect(cancelled).toBe(idleFlow);
    expect(deleteReducer(cancelled,{type:'previewLoaded',requestId:'req-1',preview:readyPreview})).toBe(idleFlow);
  });

  it('요청 ID가 다른 응답(이전에 열었던 확인의 늦은 응답)은 무시한다',()=>{
    const reopened=deleteReducer(deleteReducer(open(),{type:'cancel'}),{type:'open',runId:'run-A',requestId:'req-2'});
    expect(deleteReducer(reopened,{type:'previewLoaded',requestId:'req-1',preview:readyPreview})).toBe(reopened);
    const pending=deleteReducer(deleteReducer(ready(),{type:'confirm'}),{type:'succeeded',requestId:'other',result});
    expect(pending.phase).toBe('busy');
  });

  it('armedFlowFor는 idle·done·다른 실행에는 null',()=>{
    const done:DeleteFlow={phase:'done',runId:'run-A',requestId:'req-1',result};
    expect(armedFlowFor(idleFlow,'run-A')).toBeNull();
    expect(armedFlowFor(done,'run-A')).toBeNull();
    expect(armedFlowFor(ready(),'run-A')?.phase).toBe('ready');
    expect(armedFlowFor(ready(),'run-Z')).toBeNull();
  });
});

describe('오류 문구',()=>{
  it('접두 코드를 한국어 문구로 바꾼다',()=>{
    expect(describeDeleteError(new Error('run_running: x'))).toContain('진행 중인 실행');
    expect(describeDeleteError(new Error('has_dependents: x'))).toContain('문맥으로 쓴 실행');
    expect(describeDeleteError(new Error('confirm_mismatch: x'))).toContain('일치하지 않습니다');
    expect(describeDeleteError(new Error('not_found: x'))).toContain('찾을 수 없');
  });
  it('알 수 없는 오류는 원문(Error: 접두 제거)을 보인다',()=>{
    expect(describeDeleteError(new Error('HTTP 500'))).toBe('HTTP 500');
    expect(describeDeleteError('Error: boom')).toBe('boom');
  });
});

describe('성공 뒤 동작(순수 함수)',()=>{
  it('재생 중인 실행이 삭제됐을 때만 실시간으로 돌아간다',()=>{
    expect(shouldLeaveReplay('run-A','run-A')).toBe(true);
    expect(shouldLeaveReplay('run-B','run-A')).toBe(false);
    expect(shouldLeaveReplay(null,'run-A')).toBe(false);
  });

  it('열린 사건·보고서·실행 상세 창은 과거 기록 목록으로, 나머지는 그대로',()=>{
    const list={kind:'과거 기록'} as const;
    expect(popupAfterDelete({kind:'event',id:'e',local:false})).toEqual(list);
    expect(popupAfterDelete({kind:'report',id:'r'})).toEqual(list);
    expect(popupAfterDelete({kind:'history',runId:'run-A'})).toEqual(list);
    expect(popupAfterDelete(list)).toBeNull();
    expect(popupAfterDelete(null)).toBeNull();
    expect(popupAfterDelete({kind:'안내'})).toBeNull();
  });

  it('알림 문구: 개수 요약, 경고가 있으면 그대로 덧붙인다',()=>{
    expect(scopeText(counts)).toBe('사건 12건 · 보고서 1건 · 변환 파일 2개 · 웹 자료 5건');
    expect(deleteNotice(result)).toBe('실행을 삭제했습니다. 사건 12건 · 보고서 1건 · 변환 파일 2개 · 웹 자료 5건');
    const withWarnings={...result,warnings:['보고서 파일을 지우지 못했습니다.','변환 파일 1개 삭제 실패']};
    expect(deleteNotice(withWarnings)).toBe('실행을 삭제했습니다. 사건 12건 · 보고서 1건 · 변환 파일 2개 · 웹 자료 5건 보고서 파일을 지우지 못했습니다. 변환 파일 1개 삭제 실패');
  });
});

describe('삭제 흔적 사건 숨김',()=>{
  const event=(type:string,id:string)=>({event_id:id,type} as JournalEvent);
  it('run.deleted만 걸러 낸다',()=>{
    const list=[event('plan.created','a'),event('run.deleted','b'),event('model.call','c')];
    expect(visibleEvents(list).map(e=>e.event_id)).toEqual(['a','c']);
  });
});
