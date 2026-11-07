import type {RunSnapshot} from './계약';
import {sourceLabel} from './자료모드';
import {formatRunTime,runStatusLabel,runStatusTone} from './실행표시';
// 같은 이름의 실행삭제.ts(논리)와 실행삭제.tsx(화면)가 있어 확장자를 붙여야 의도한 파일을 가리킨다.
import {DeleteButton,DeleteConfirm} from './실행삭제.tsx';
import {armedFlowFor} from './실행삭제.ts';
import type {DeleteFlow} from './실행삭제.ts';

export interface PastRecordListProps{
  runs:RunSnapshot[];
  currentRunId:string|null;
  replayRunId:string|null;
  onDetail:(runId:string)=>void;
  onReplay:(runId:string)=>void;
  // 삭제 확인은 한 번에 하나만 열리므로 흐름 하나와 콜백만 받는다. 생략하면 삭제 확인이 닫힌 목록이다.
  deleteFlow?:DeleteFlow;
  onDeleteStart?:(runId:string)=>void;
  onDeleteConfirm?:()=>void;
  onDeleteCancel?:()=>void;
}

// 최신 실행이 위로 오도록 created_at 내림차순으로 정렬한다.
// 시각이 같으면(동시 생성) 서버가 준 순서의 역순을 유지해 기존 목록의 '나중 것이 위' 동작을 보존한다.
function newestFirst(runs:RunSnapshot[]):RunSnapshot[]{
  return runs
    .map((run,index)=>({run,index,time:Date.parse(run.created_at)}))
    .sort((a,b)=>{
      const ta=Number.isNaN(a.time)?-Infinity:a.time;
      const tb=Number.isNaN(b.time)?-Infinity:b.time;
      return tb-ta||b.index-a.index;
    })
    .map(entry=>entry.run);
}

// 계획 단계의 report_title은 보고서 제출 전에도 채워진다.
// 그래서 제출된 보고서(report_id)가 있을 때만 제목을 보고서처럼 보여 준다.
function reportLine(run:RunSnapshot):string{
  if(!run.report_id)return '보고서 없음';
  return `보고서: ${run.report_title||'제목 없음'}`;
}

function renderItem(run:RunSnapshot,props:PastRecordListProps){
  const isCurrent=run.run_id===props.currentRunId;
  const isReplaying=run.run_id===props.replayRunId;
  const status=run.status;
  const armed=props.deleteFlow?armedFlowFor(props.deleteFlow,run.run_id):null;
  return (
    <li key={run.run_id} data-testid="history-item" data-run-id={run.run_id} data-current={isCurrent} data-replaying={isReplaying}>
      <h3 className="record-question" title={run.question}>{run.question}</h3>
      <div className="record-chips">
        <span className="record-chip" data-tone={runStatusTone(status)} data-status={status} title={status}>{runStatusLabel(status)}</span>
        <span className="record-chip" data-kind="source">{sourceLabel(run.source_mode??'local')}</span>
        <time className="record-chip" dateTime={run.created_at}>{formatRunTime(run.created_at)}</time>
        {isCurrent&&<span className="record-chip" data-kind="current">현재 실행</span>}
        {isReplaying&&<span className="record-chip" data-kind="replaying">재생 중</span>}
      </div>
      <p className="record-report">{reportLine(run)}</p>
      <div className="history-actions">
        <button onClick={()=>props.onDetail(run.run_id)}>자세히 보기</button>
        <button onClick={()=>props.onReplay(run.run_id)}>기록 재생</button>
        <DeleteButton run={run} onStart={runId=>props.onDeleteStart?.(runId)}/>
      </div>
      {armed&&<DeleteConfirm flow={armed} onConfirm={()=>props.onDeleteConfirm?.()} onCancel={()=>props.onDeleteCancel?.()}/>}
    </li>
  );
}

export function PastRecordList(props:PastRecordListProps){
  if(!props.runs.length)return <p>보존된 실행이 없습니다.</p>;
  return <ol className="record-list">{newestFirst(props.runs).map(run=>renderItem(run,props))}</ol>;
}
