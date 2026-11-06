import {useEffect,useRef} from 'react';
import {draggedHeight,effectiveDrawerHeight,keyboardHeight} from './드로어크기';
import type {RunSnapshot} from './계약';
import type {RunArchive} from './시간계약';
import {measuredElapsed,offsetMs,replayEnd} from './시간재생';
import {actors,names} from './마을배치';
import './실행타임라인.css';
const seconds=(ms:number|null)=>ms!==null&&Number.isFinite(ms)&&ms>=0?(ms<10?`${ms.toFixed(1)}ms`:ms<1000?`${Math.round(ms)}ms`:`${(ms/1000).toFixed(2)}초`):'미측정';
const phaseNames:Record<string,string>={plan:'기획',dispatch:'업무 배정',research:'조사',review:'검토',synthesize:'종합',evaluate:'품질 검사',publish:'문서 저장',model:'응답 대기'};
const phaseName=(name:string)=>phaseNames[name]??name;
const eventTrack=(actor:string,type:string)=>type==='report.submitted'?'document':actors.includes(actor as typeof actors[number])?actor:'code';
function Tracks({p}:{p:TimelineProps}){
 const a=p.archive!;
 const end=Math.max(1,replayEnd(a),p.replay?0:p.at),pct=(ms:number)=>`${Math.min(100,Math.max(0,ms/end*100))}%`;
 const tracks=[...actors,'code','document',...new Set(a.spans.map(s=>s.track_id).filter(id=>![...actors,'code','document'].includes(id)))];
 return <>
 <div className="timeline-tracks">{tracks.map(track=>{const trackSpans=a.spans.filter(s=>s.track_id===track);const total=measuredElapsed({...a,spans:trackSpans},p.replay?undefined:p.at);return <div className="timeline-track" key={track} data-testid={`timeline-track-${track}`}><strong>{names[track as typeof actors[number]]??(track==='code'?'코드':'문서')}<small data-testid={`timeline-total-${track}`}>{seconds(total)}{trackSpans.some(s=>s.status==='running')?' · 진행 중':''}</small></strong><div className="track-lanes">
 <i className="timeline-playhead" style={{left:pct(p.at)}}/>
 {a.spans.filter(s=>s.track_id===track).map(s=>{
  const start=offsetMs(a,s.started_at),finish=s.ended_at?offsetMs(a,s.ended_at):null;
  if(start===null)return null;
  const duration=s.duration_ms!==null&&Number.isFinite(s.duration_ms)&&s.duration_ms>=0?s.duration_ms:null;
  const width=Math.max(0,(finish??(s.status==='running'?p.at:start))-start);
  const event=s.event_ids.find(id=>a.events.some(e=>e.event_id===id));
  const tooltip=`${phaseName(s.label)} · ${s.kind==='model_wait'?'응답 대기':'단계 전체 · 대기 포함'} · ${seconds(duration)} · ${s.status} · ${s.timing_source}${finish===null?` · 미종료${s.status==='running'?` · 현재 경과 ${seconds(width)}`:''}`:''}`;
  return <button key={s.span_id} data-testid={`timeline-span-${s.span_id}`} className={`timeline-span ${s.kind} ${s.status}`} style={{left:pct(start),width:pct(width)}} title={tooltip} aria-label={tooltip} disabled={!event} onClick={()=>event&&p.onEvent(event)}><span>{phaseName(s.label)}{duration!==null?` · ${seconds(duration)}`:''}</span></button>;
 })}
 {a.events.filter(e=>eventTrack(e.actor,e.type)===track).map(e=>{const at=offsetMs(a,e.occurred_at);return at===null?null:<button key={e.event_id} className={`timeline-event ${e.status}`} data-event-id={e.event_id} title={`${e.summary} · ${seconds(at)} · ${e.event_id}`} aria-label={`${e.summary} 사건 상세`} style={{left:pct(at)}} onClick={()=>p.onEvent(e.event_id)}/>;})}
 {track==='document'&&a.events.filter(e=>e.type==='report.submitted').map(e=>{const start=offsetMs(a,e.occurred_at);return start===null?null:<button key={`visual-${e.event_id}`} className="timeline-span visual" style={{left:pct(start),width:pct(2200)}} title="문서 운반 연출 · 2.20초 · 측정한 코드 시간이 아님" onClick={()=>p.onEvent(e.event_id)}>운반 연출</button>;})}
 </div></div>;})}</div>
 </>;
}

function TimeAxis({p}:{p:TimelineProps}){
 const a=p.archive!;
 const end=Math.max(1,replayEnd(a),p.replay?0:p.at);
 return <>
 <div className="timing-summary" title="단계 전체는 응답 대기를 포함합니다. 겹친 구간은 한 번만 셉니다.">실제 경과 {seconds(a.run.status==='running'&&!p.replay?Math.max(a.duration_ms,p.at):a.duration_ms)} · 겹침 제외 작업 경과 {seconds(measuredElapsed(a,p.replay?undefined:p.at))}{!a.has_measured_spans&&<small>세부 시간 미측정 · 보존된 사건 시점만 표시합니다.</small>}</div>
 <div className="timeline-legend" aria-label="시간 막대 범례"><span className="kind-node">단계 전체 · 대기 포함</span><span className="kind-wait">응답 대기</span><span className="kind-visual">운반 연출 · 측정 아님</span></div>
 <label className="timeline-seek">기록 시점 <input type="range" data-testid="timeline-scrubber" aria-label="기록 시점 밀리초" min={0} max={end} step={1} value={Math.min(p.at,end)} onChange={e=>p.onSeek(Number(e.target.value))}/></label>
 <div className="timeline-axis"><span>객체 / 역할</span><div>{[0,.25,.5,.75,1].map(n=><time key={n} style={{left:`${n*100}%`}}>{seconds(end*n)}</time>)}</div></div>
 </>;
}

export interface TimelineProps {height?:number;minHeight?:number;maxHeight?:number;onHeightChange?:(height:number)=>void;open:boolean;runs:RunSnapshot[];runId:string|null;archive:RunArchive|null;at:number;replay:boolean;playing:boolean;loading:boolean;error:string;onToggle:()=>void;onSelect:(id:string)=>void;onSeek:(ms:number)=>void;onPlay:()=>void;onStage:(direction:-1|1)=>void;onLive:()=>void;onRetry:()=>void;onEvent:(id:string)=>void}
export function Timeline(p:TimelineProps){
 const drag=useRef<{id:number;y:number;height:number;target:HTMLDivElement}|null>(null);
 function finish(){
  const active=drag.current;drag.current=null;
  if(active?.target.hasPointerCapture(active.id))active.target.releasePointerCapture(active.id);
 }
 useEffect(()=>{if(!p.open)finish();return finish;},[p.open]);
 const bounds={min:p.minHeight??184,max:p.maxHeight??900};
 return <>
 <button className="timeline-toggle" data-testid="timeline-toggle" aria-expanded={p.open} aria-controls="timing-drawer" aria-label={p.open?'실행 타임라인 닫기':'실행 타임라인 열기'} onClick={p.onToggle}><span/>{p.open?'기록 접기':'실행 타임라인'}</button>
 {p.open&&<section id="timing-drawer" className="timeline-drawer" data-compact={(p.height??360)<300} data-testid="timeline-drawer" aria-label="실행 시간 기록">
  <div data-testid="timeline-resize-handle" className="timeline-resize-handle" role="separator" tabIndex={0} aria-orientation="horizontal" aria-label="타임라인 높이 조절" aria-controls="timing-drawer" aria-valuemin={bounds.min} aria-valuemax={bounds.max} aria-valuenow={Math.round(p.height??360)} aria-valuetext={`${Math.round(p.height??360)}픽셀 · 위/아래 방향키, Home 최소, End 최대`} title="위아래로 끌거나 방향키로 높이 조절"
   onPointerDown={e=>{if(!p.onHeightChange||!e.isPrimary||e.button!==0)return;e.preventDefault();e.stopPropagation();finish();e.currentTarget.focus();e.currentTarget.setPointerCapture(e.pointerId);drag.current={id:e.pointerId,y:e.clientY,height:p.height??360,target:e.currentTarget};}}
   onPointerMove={e=>{const active=drag.current;if(!active||active.id!==e.pointerId)return;e.preventDefault();p.onHeightChange?.(effectiveDrawerHeight(draggedHeight(active.height,active.y,e.clientY),bounds));}}
   onPointerUp={e=>{if(drag.current?.id===e.pointerId)finish();}} onPointerCancel={e=>{if(drag.current?.id===e.pointerId)finish();}} onLostPointerCapture={e=>{if(drag.current?.id===e.pointerId)finish();}}
   onKeyDown={e=>{const height=keyboardHeight(e.key,p.height??360,bounds,e.shiftKey);if(height===null||!p.onHeightChange)return;e.preventDefault();e.stopPropagation();p.onHeightChange(height);}}/>
  <div className="timeline-toolbar" data-testid="timeline-toolbar"><header className="timeline-header"><label>실행 <select data-testid="timeline-run-select" value={p.runId??''} onChange={e=>p.onSelect(e.target.value)}><option value="">실시간 실행</option>{p.runs.map(r=><option value={r.run_id} key={r.run_id}>{r.question} · {r.status} · {r.run_id}</option>)}</select></label><button aria-label="실행 타임라인 닫기" onClick={p.onToggle}>닫기</button></header>
  <div className="timeline-controls"><button data-testid="replay-prev" disabled={!p.archive} onClick={()=>p.onStage(-1)}>이전 단계</button><button data-testid="replay-play" disabled={!p.archive} onClick={p.onPlay}>{p.playing?'일시정지':'재생'}</button><button data-testid="replay-next" disabled={!p.archive} onClick={()=>p.onStage(1)}>다음 단계</button><button data-testid="replay-live" onClick={p.onLive}>실시간 복귀</button><span data-testid="replay-status" role="status">{p.replay?`읽기 전용 기록 · ${p.playing?'재생 중':'일시정지'}`:'실시간'}</span><output data-testid="replay-time">{(p.at/1000).toFixed(2)}초</output></div>
  <div className="timeline-messages">{!p.runs.length&&!p.archive&&!p.loading&&<p>보존된 실행이 없습니다. 지시를 실행하면 사건과 측정 구간이 표시됩니다.</p>}{p.loading&&<p role="status">전체 기록을 불러오는 중입니다.</p>}{p.error&&<p role="alert">{p.error} <button onClick={p.onRetry}>기록 다시 조회</button></p>}</div>{p.archive&&<TimeAxis p={p}/>}</div>
  <div className="timeline-track-scroll" data-testid="timeline-track-scroll">{p.archive&&<Tracks p={p}/>}</div>
 </section>}
 </>;
}
