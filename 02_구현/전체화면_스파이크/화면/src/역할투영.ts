import type {Snapshot,RunSnapshot,JournalEvent} from './계약';
import type {Actor} from './마을배치';
import {currentRunEvents} from './상태';
export function actorState(actor:Actor,snapshot:Snapshot|null,current:RunSnapshot|undefined):{status:string;event:JournalEvent|undefined;summary?:string}{
 if(!snapshot||!current)return {status:'idle',event:undefined};
 const event=currentRunEvents(snapshot.events,current).filter(e=>e.type!=='result.superseded'&&e.status!=='superseded'&&(e.actor===actor||e.recipient===actor)).sort((a,b)=>b.seq-a.seq)[0];
 const waiting=snapshot.waiting.some(g=>!g.is_stale&&g.run_id===current.run_id&&g.control_revision===current.control_revision&&g.actor===actor);
 const status=current.status!=='running'?current.status:waiting?'waiting':!event?'idle':event.type==='assignment.created'?'assigned':event.type==='model.started'?'running':event.status||'completed';
 const topic=actor==='planner'?current.question:current.plan?.find(item=>item.assignee===actor)?.title;
 const labels:Record<string,string>={waiting:snapshot.provider==='openai'?'모델 응답 대기':'모의 응답 대기',assigned:'조사 배정',completed:'작업 기록 준비',running:'작업 중',working:'작업 중',failed:'실행 실패',cancelled:'실행 취소',blocked:'필수 검사 불합격',interrupted:'재시작으로 중단'};
 const original=event?.summary&&event.summary!==event.type?event.summary:undefined;
 const summary=!event?undefined:original??(actor==='planner'?`지시: ${topic||'현재 지시'}`:`${topic||'종합 작업'} · ${labels[status]||'사건 기록 확인'}`);
 return {status,event,summary};
}
