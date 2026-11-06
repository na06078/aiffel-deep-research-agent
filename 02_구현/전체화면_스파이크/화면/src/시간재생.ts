import type {RunArchive} from './시간계약';
import type {SceneClock} from './장면계약';
import {actors} from './마을배치';
export function sceneClock(archive:RunArchive,at_ms:number,replay:boolean,priorReport?:Report|null):SceneClock {
 const projection=projectArchive(archive,at_ms);
 const clock:SceneClock={at_ms,replay,actor_started_ms:{}};
 const mark=(actor:string,ms:number|null)=>{if(actors.includes(actor as typeof actors[number])&&ms!==null&&ms<=at_ms){const key=actor as typeof actors[number];clock.actor_started_ms[key]=Math.min(clock.actor_started_ms[key]??Infinity,ms);}};
 for(const e of projection.events){
  if(e.control_revision!==projection.run.control_revision||e.type==='result.superseded')continue;
  const ms=offsetMs(archive,e.occurred_at);
  if(e.type==='instruction.received'||e.type==='model.started')mark(e.actor,ms);
  if(e.type==='assignment.created')mark(e.recipient??(e.output as Assignment)?.assignee,ms);
  if(e.type==='report.submitted'&&ms!==null)clock.report_submitted_ms=ms;
 }
 for(const span of archive.spans){if(span.control_revision===projection.run.control_revision)mark(span.actor,offsetMs(archive,span.started_at));}
 // A previous completed run can still have its paper on the live submission desk.
 // Negative offsets are its recorded timestamp relative to the new run, not a fabricated delay.
 if(!replay&&priorReport&&priorReport.run_id!==archive.run.run_id&&clock.report_submitted_ms===undefined){
  const submitted=Date.parse(priorReport.created_at)-Date.parse(archive.started_at);
  if(Number.isFinite(submitted)&&submitted<=at_ms)clock.report_submitted_ms=submitted;
 }
 return clock;
}
export function adjacentStage(archive:RunArchive,at:number,direction:-1|1):number {const bounds=stageTimes(archive);return direction<0?bounds.filter(n=>n<at).at(-1)??0:bounds.find(n=>n>at)??replayEnd(archive);}
export function stageTimes(archive:RunArchive):number[] {
 const values=[0,replayEnd(archive),...archive.events.map(e=>offsetMs(archive,e.occurred_at)),...archive.spans.flatMap(s=>[offsetMs(archive,s.started_at),s.ended_at?offsetMs(archive,s.ended_at):null])];
 return [...new Set(values.filter((n):n is number=>n!==null&&Number.isFinite(n)&&n>=0))].sort((a,b)=>a-b);
}
export function replayEnd(archive:RunArchive):number {
 const reportTimes=archive.events.filter(e=>e.type==='report.submitted').map(e=>offsetMs(archive,e.occurred_at)).filter((n):n is number=>n!==null);
 return Math.max(archive.duration_ms,...reportTimes.map(n=>n+2200));
}
export function measuredElapsed(archive:RunArchive,observedAt?:number):number|null {
 const ranges=archive.spans.flatMap(s=>{
  const open=s.status==='running'&&!s.ended_at&&observedAt!==undefined&&Number.isFinite(observedAt)&&observedAt>=0;
  const start=offsetMs(archive,s.started_at),end=s.ended_at?offsetMs(archive,s.ended_at):open?observedAt!:null;
  const measured=s.duration_ms!==null&&Number.isFinite(s.duration_ms)&&s.duration_ms>=0;
  return start!==null&&end!==null&&end>=start&&(measured||open)?[[start,end]]:[];
 }).sort((a,b)=>a[0]-b[0]);
 if(!ranges.length)return null;
 let sum=0,[start,end]=ranges[0];
 for(const [a,b] of ranges.slice(1)){if(a<=end)end=Math.max(end,b);else{sum+=end-start;start=a;end=b;}}
 return sum+end-start;
}
import type {Assignment,EventDetail,Report,RunSnapshot,Snapshot,Gate} from './계약';

export interface ReplayProjection {provider:RunArchive['provider'];run:RunSnapshot;events:EventDetail[];report:Report|null;waiting:Gate[]}
export function offsetMs(archive:RunArchive,iso:string):number|null {
 const n=Date.parse(iso)-Date.parse(archive.started_at);
 return Number.isFinite(n)&&n>=0?n:null;
}
function emptyRun(archive:RunArchive):RunSnapshot {
 return {run_id:archive.run.run_id,question:'',control_revision:0,status:'running',phase:'not_started',plan:[],report_id:null,draft:null,quality:{passed:null,reasons:[]},parent_report_id:null,created_at:archive.started_at,updated_at:archive.started_at,mock_requests:0,actual_model_requests:0};
}
// Rebuild, never undo: no API, wall clock or mutable live snapshot enters this projection.
export function projectArchive(archive:RunArchive,at_ms:number):ReplayProjection {
 let run=emptyRun(archive),report:Report|null=null;
 const waiting=new Map<string,Gate>();
 const events=archive.events.filter(e=>e.run_id===run.run_id&&offsetMs(archive,e.occurred_at)!==null&&(offsetMs(archive,e.occurred_at)!)<=at_ms)
  .slice().sort((a,b)=>Date.parse(a.occurred_at)-Date.parse(b.occurred_at)||a.seq-b.seq);
 for(const e of events){
  if(e.type==='instruction.received'){
   run={...emptyRun(archive),control_revision:e.control_revision,question:typeof (e.input as {text?:unknown})?.text==='string'?(e.input as {text:string}).text:'',phase:'planning',updated_at:e.occurred_at};
   report=null;waiting.clear();
  }
  if(e.control_revision!==run.control_revision||e.type==='result.superseded'||e.status==='superseded')continue;
  run={...run,updated_at:e.occurred_at};
  switch(e.type){
   case 'plan.created':run={...run,plan:Array.isArray(e.output)?structuredClone(e.output) as Assignment[]:[],phase:'researching'};break;
   case 'model.started':{
    run=e.provider==='openai'?{...run,actual_model_requests:run.actual_model_requests+1}:{...run,mock_requests:run.mock_requests+1};
    const gateId=(e.output as {gate_id?:string})?.gate_id;
    if(gateId)waiting.set(e.actor,{gate_id:gateId,run_id:e.run_id,control_revision:e.control_revision,actor:e.actor,section_id:(e.input as {assignment?:Assignment})?.assignment?.section_id??'',created_at:e.occurred_at,is_stale:false});
    break;
   }
   case 'model.completed':waiting.delete(e.actor);break;
   case 'draft.created':run={...run,draft:typeof e.output==='string'?e.output:null,phase:'evaluating'};break;
   case 'quality.evaluated':{
    const quality=e.output as RunSnapshot['quality'];
    run={...run,quality:structuredClone(quality),...(quality.passed===false?{status:'blocked' as const,phase:'quality_failed'}:{})};break;
   }
   case 'report.submitted':report=structuredClone(e.output) as Report;run={...run,report_id:report.report_id,status:'completed',phase:'submitted'};waiting.clear();break;
   case 'model.failed':case 'execution.failed':run={...run,status:'failed',phase:'failed'};waiting.clear();break;
   case 'run.cancelled':run={...run,status:'cancelled',phase:'cancelled'};waiting.clear();break;
   case 'run.interrupted':run={...run,status:'interrupted',phase:'interrupted'};waiting.clear();break;
  }
 }
 return {provider:archive.provider,run,events,report,waiting:[...waiting.values()]};
}
export function projectionSnapshot(projection:ReplayProjection):Snapshot {
 return {protocol_version:1,mode:projection.provider==='openai'?'research':'spike',provider:projection.provider,current_run_id:projection.run.run_id,runs:[projection.run],queue:[],queue_paused:false,queue_pause_reason:null,waiting:projection.waiting,events:projection.events,events_has_more:false,high_watermark_seq:Math.max(0,...projection.events.map(e=>e.seq)),mock_requests:projection.run.mock_requests,actual_model_requests:projection.run.actual_model_requests};
}
