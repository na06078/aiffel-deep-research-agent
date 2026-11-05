import {useEffect,useRef,useState} from 'react';
import type {EventDetail,JournalEvent,ReleaseOutcome,Report} from './계약';
import {api,sendCommand} from './통신';
import {gateTargets,selectionResult,admittedDraft,formatEventTime,currentRunEvents} from './상태';
import {retryIdentity,scenePhase,type Phase,type SceneAction} from './장면상태';
import {useJournal} from './실행기록';
import {Village} from './마을';
import {Inspector,ReportViewer} from './상세보기';
const roleNames:Record<string,string>={planner:'기획자',dispatcher:'배정', 'researcher-1':'조사관 1','researcher-2':'조사관 2','researcher-3':'조사관 3','researcher-4':'조사관 4',reviewer:'검토',editor:'종합',evaluator:'품질 검사',publisher:'제출'};
const phases:Record<Phase,string>={idle:'준비',moving:'이동 중','at-workstation':'작업대 도착',working:'합성 작업 중',worked:'문서 생성',delivering:'문서 운반 중',submitted:'문서 내려놓음'};
function route(){return location.hash==='#/control'?'control':'assets';}
function newArtEvent(action:string,status:string,output:unknown):EventDetail{
 return {event_id:`local-${crypto.randomUUID()}`,seq:0,run_id:'local-scene',control_revision:0,occurred_at:new Date().toISOString(),type:`scene.${action}`,actor:action==='work'?'workstation':'planner',recipient:null,status,summary:action==='move'?'조정자 이동':action==='work'?'작업대 합성 출력':action==='deliver'?'합성 문서 운반': '장면 초기화',input:{action,source:'LOCAL SYNTHETIC',model_calls:0},output,error:null,provider:'art-demo'};
}
export function App(){
 const [mode,setMode]=useState(route);
 const {snapshot,refresh,connection,error:journalError}=useJournal();
 const [detail,setDetail]=useState<EventDetail|null>(null),[detailLoading,setDetailLoading]=useState(false);
 const selectionToken=useRef(0);
 const [report,setReport]=useState<Report|null>(null);
 const [draft,setDraft]=useState(()=>sessionStorage.getItem('spike-draft')||'');
 const [sending,setSending]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const pendingRequest=useRef<{text:string;id:string}|null>(null);
 const [artEvents,setArtEvents]=useState<EventDetail[]>([]);
 const [phase,setPhase]=useState<Phase>('idle'),[motionStarted,setMotionStarted]=useState(0);
 const animationTimer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const [transport,setTransport]=useState<{run_id:string;report_id:string}|null>(null);
 const [transportPhase,setTransportPhase]=useState<Phase>('idle');
 const seenReports=useRef(new Set<string>());
 const current=snapshot?.runs.find(run=>run.run_id===snapshot.current_run_id);
 const recent=snapshot?.events||[];
 const currentEvents=currentRunEvents(recent,current).sort((a,b)=>b.seq-a.seq);
 const currentEventIds=new Set(currentEvents.map(event=>event.event_id));
 useEffect(()=>{const changed=()=>setMode(route());window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed);},[]);
 useEffect(()=>{sessionStorage.setItem('spike-draft',draft);},[draft]);
 useEffect(()=>()=>clearTimeout(animationTimer.current),[]);
 useEffect(()=>{
  const published=snapshot?.runs.filter(run=>run.report_id&&run.status==='completed')||[];
  const newest=[...published].reverse().find(run=>!seenReports.current.has(run.report_id!));
  published.forEach(run=>seenReports.current.add(run.report_id!));
  if(!newest)return;
  setTransport({run_id:newest.run_id,report_id:newest.report_id!});setTransportPhase('delivering');setMotionStarted(performance.now());
  const timer=setTimeout(()=>setTransportPhase('submitted'),2200);return()=>clearTimeout(timer);
 },[snapshot?.runs.filter(run=>run.report_id).map(run=>run.report_id).join('|')]);
 function chooseArt(event:EventDetail){selectionToken.current++;setDetailLoading(false);setDetail(event);}
 async function chooseEvent(event:JournalEvent){
  const token=++selectionToken.current;setDetailLoading(true);setError('');
  try{const result=await api.detail(event.event_id);setDetail(old=>selectionResult(old,selectionToken.current,token,result));}
  catch(e){if(token===selectionToken.current)setError(String(e));}
  finally{if(token===selectionToken.current)setDetailLoading(false);}
 }
 async function openReport(id:string){try{setReport(await api.report(id));setError('');}catch(e){setError(String(e));}}
 function scene(action:SceneAction){
  clearTimeout(animationTimer.current);setMotionStarted(performance.now());setPhase(scenePhase(action,0));
  const began=newArtEvent(action,action==='reset'?'completed':'started',{phase:scenePhase(action,0),note:'사용자가 누른 로컬 장면 동작'});
  setArtEvents(old=>[began,...old].slice(0,40));
  if(action==='reset')return;
  const duration=action==='move'?1500:action==='work'?1600:2200;
  const start=performance.now();animationTimer.current=setTimeout(()=>{
   const finalPhase=scenePhase(action,duration);setPhase(finalPhase);
   const finished=newArtEvent(action,'completed',{phase:finalPhase,elapsed_ms:Math.round(performance.now()-start),document:action==='deliver'?'합성 종이를 제출 위치에 내려놓음':action==='work'?'로컬 합성 문서 생성':null});
   setArtEvents(old=>[finished,...old].slice(0,40));
  },duration);
 }
 async function submit(event:React.FormEvent){
  event.preventDefault();if(!draft.trim()||sending)return;
  const text=draft;const identity=retryIdentity(pendingRequest.current,text,()=>crypto.randomUUID());pendingRequest.current=identity;
  setSending(true);setError('');location.hash='/control';setMode('control');
  try{const admission=await sendCommand(text,identity.id);pendingRequest.current=null;setDraft(value=>admittedDraft(value,text));
   setNotice(`접수됨 · ${admission.instruction_id} · 적용 여부는 instruction.applied 사건에서 확인하세요.`);
   await refresh().catch(e=>setError(`접수 후 조회 실패: ${String(e)} · 재전송하지 말고 연결 복원을 기다리세요.`));
  }catch(e){setError(`${String(e)} · 입력을 보존했습니다. 같은 입력으로 재시도할 수 있습니다.`);}
  finally{setSending(false);}
 }
 async function mutation(work:()=>Promise<unknown>,label:string){setBusy(true);setError('');try{await work();setNotice(`${label} 요청 처리됨 · 아래 서버 기록을 확인하세요.`);await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
 function release(kind:'one'|'all'|'stale',outcome:ReleaseOutcome){const ids=gateTargets(snapshot?.waiting||[],kind);if(ids.length)void mutation(()=>api.release(ids,outcome),'대역 응답 해제');}
 const localPlanner=artEvents.find(e=>e.actor==='planner');const localWork=artEvents.find(e=>e.actor==='workstation');
 function latestActor(actor:string){return currentEvents.find(event=>event.actor===actor);}
 const planner=mode==='assets'?localPlanner:latestActor('planner');
 const workstation=mode==='assets'?localWork:currentEvents.find(event=>['dispatcher','editor','evaluator','publisher','reviewer'].includes(event.actor));
 const currentGates=gateTargets(snapshot?.waiting||[],'all'),staleGates=gateTargets(snapshot?.waiting||[],'stale');
 const visiblePhase=mode==='assets'?phase:transport?transportPhase:(current&&snapshot?.waiting.some(g=>!g.is_stale&&g.run_id===current.run_id)?'at-workstation':'idle');
 const previousDocument=mode==='control'&&transport!==null&&transport.run_id!==current?.run_id;
 function paperClick(){if(mode==='control'){if(transport)void openReport(transport.report_id);}else{const event=artEvents.find(e=>e.type==='scene.deliver'&&e.status==='completed')||artEvents.find(e=>e.type==='scene.work'&&e.status==='completed');if(event)chooseArt(event);}}
 function bubbleSelect(event:JournalEvent|undefined){if(!event)return;if(mode==='assets')chooseArt(event as EventDetail);else void chooseEvent(event);}
 const paperVisible=mode==='assets'?['worked','delivering','submitted'].includes(phase):!!transport;
 return <>
  <header className="topbar"><div className="brand"><h1>딥리서치 에이전트</h1><span className="spike">스파이크</span></div><nav aria-label="실험 모드"><a href="#/assets" aria-current={mode==='assets'?'page':undefined}>에셋 실험</a><a href="#/control" aria-current={mode==='control'?'page':undefined}>실행 제어</a></nav><p className="synthetic">모의 응답 · 실제 GPT 호출 없음</p></header>
  <main>
   <div className="workspace">
    <section className="scene-panel" aria-label="마을과 실험 조작">
     <div className="section-heading"><h2>{mode==='assets'?'작은 마을, 세 가지 동작':'서버 기록과 연결된 마을'}</h2><span className="scene-status" role="status">{previousDocument?'이전 제출 문서 · ':''}{phases[visiblePhase]}</span></div>
     <Village phase={visiblePhase} motionStarted={motionStarted} paperVisible={paperVisible} onPaper={paperClick}>
      <span className="map-label office">조사 사무소</span><span className="map-label archive">문서 보관소</span>
      <button type="button" className="bubble planner-bubble" data-testid="bubble-planner" disabled={!planner} onClick={()=>bubbleSelect(planner)}><strong>기획자</strong><span>{planner?.summary||(mode==='assets'?'이동 보기로 장면을 시작하세요.':'기획 사건을 기다립니다.')}</span><small>(클릭해서 더 자세히)</small></button>
      <button type="button" className="bubble workstation-bubble" data-testid="bubble-workstation" aria-label="작업대 사건 상세" disabled={!workstation} onClick={()=>bubbleSelect(workstation)}><span>...</span><small>(클릭해서 더 자세히)</small></button>
      <span className="map-label work-label">작업대 · 출력기</span>
      {paperVisible&&<button type="button" className="paper-hit" data-testid="paper-document" onClick={paperClick} aria-label={mode==='assets'?'로컬 합성 문서 상세':'확정 보고서 열기'}><svg viewBox="0 0 20 24" aria-hidden="true"><path d="M3 1h10l5 5v17H3z" fill="#fff9e9" stroke="#52694e"/><path d="M6 10h9M6 14h9M6 18h6" stroke="#52694e"/></svg><span>{mode==='assets'?'합성 종이':previousDocument?'이전 제출 문서':'제출 문서'}</span></button>}
     </Village>
     <div className="role-strip" aria-label="역할과 실제 사건">{Object.entries(roleNames).map(([actor,label])=>{
      const event=latestActor(actor);const waiting=mode==='control'&&snapshot?.waiting.some(g=>g.actor===actor&&!g.is_stale&&g.run_id===snapshot.current_run_id);
      return <button type="button" key={actor} disabled={mode==='assets'||!event} onClick={()=>event&&void chooseEvent(event)} title={actor}><span className={waiting?'role-dot waiting':'role-dot'}/>{label}<small>{mode==='assets'?'장면':waiting?'대역 대기':event?.status||'기록 없음'}</small></button>;
     })}</div>
     {mode==='assets'?<div className="experiment-controls"><h3>로컬 합성 장면</h3><p>서버 작업을 만들지 않는 에셋 실험입니다. 정면 포즈를 코드로 이동합니다.</p><div className="button-row"><button type="button" onClick={()=>scene('move')}>이동 보기</button><button type="button" onClick={()=>scene('work')}>작업 보기</button><button type="button" onClick={()=>scene('deliver')}>문서 운반</button><button type="button" className="quiet" onClick={()=>scene('reset')}>처음으로</button></div></div>:<>
      <div className="run-summary"><h3>{current?.question||'아직 시작한 실행이 없습니다'}</h3><p>{current?`${current.status} · ${current.phase} · 버전 ${current.control_revision}`:'아래 입력창에 첫 번째 조사 지시를 입력하세요.'}</p>{current&&<p className="identifier">{current.run_id}</p>}<p className="muted">{connection} · 대역 요청 {snapshot?.mock_requests??'—'}회 · 실제 모델 0회</p>{journalError&&<p role="alert">{journalError}</p>}</div>
      <div className="experiment-controls"><h3>테스트 대역 제어</h3><p>현재 대기 {currentGates.length}건 · 이전 버전 대기 {staleGates.length}건. 고유 gate ID에만 응답합니다.</p><div className="button-row">
       <button type="button" disabled={busy||!currentGates.length} onClick={()=>release('one','success')}>현재 1건 완료</button><button type="button" disabled={busy||!currentGates.length} onClick={()=>release('all','success')}>현재 모두 완료</button><button type="button" disabled={busy||!staleGates.length} onClick={()=>release('stale','success')}>이전 응답 돌려주기</button><button type="button" disabled={busy||!currentGates.length} onClick={()=>release('all','failure')}>실패 응답</button><button type="button" disabled={busy||!currentGates.length} onClick={()=>release('all','quality_fail')}>불합격 초안</button>
       <button type="button" className="danger" disabled={busy||current?.status!=='running'} onClick={()=>current&&void mutation(()=>api.cancel(current.run_id,current.control_revision),'현재 버전 취소')}>현재 실행 취소</button>
      </div><p className="muted">{!currentGates.length?'현재 해제할 대기 요청이 없어 현재 응답 버튼을 사용할 수 없습니다. ':''}{!staleGates.length?'이전 버전 대기가 없으면 이전 응답 버튼은 비활성입니다.':''}</p></div>
      <section className="queue"><div className="section-heading"><h3>대기 지시</h3>{snapshot?.queue_paused&&<button type="button" disabled={busy} onClick={()=>void mutation(api.continue,'큐 계속')}>큐 계속</button>}</div>{snapshot?.queue_paused&&<p className="pause">일시정지 · {snapshot.queue_pause_reason||'사유 미제공'} · 일반 입력은 큐 정지를 해제하지 않습니다.</p>}{snapshot?.queue.length?<ol>{snapshot.queue.map(item=><li key={item.instruction_id}>{item.text}<small>{item.instruction_id}</small></li>)}</ol>:<p className="muted">보관 중인 지시가 없습니다. /큐 뒤에 다음 지시를 입력할 수 있습니다.</p>}</section>
     </>}
    </section>
    <Inspector detail={detail} loading={detailLoading}/>
   </div>
   <form className="composer" onSubmit={submit}><label htmlFor="command">{mode==='assets'?'서버 실험으로 보낼 지시':'작업 중에도 새 지시를 입력하세요'}</label><div className="composer-row"><textarea id="command" data-testid="command-input" value={draft} onChange={e=>setDraft(e.target.value)} placeholder="예: 도시 녹지의 효과를 조사해 주세요. 다음 작업은 /큐 로 보관합니다." rows={2}/><button type="submit" data-testid="command-submit" disabled={sending||!draft.trim()}>{sending?'접수 중…':'지시 보내기'}</button></div><p className="muted">일반 입력은 즉시 개입 · /큐는 순서대로 보관 · 전송하면 실행 제어 모드로 이동합니다.</p>{notice&&<p role="status" className="notice">{notice}</p>}{error&&<p role="alert" className="error">{error}</p>}</form>
   {report&&<ReportViewer report={report} onClose={()=>setReport(null)}/>}
   {mode==='control'&&<section className="published"><h2>확정 문서</h2>{snapshot?.runs.some(run=>run.report_id&&run.status==='completed')?<ul>{snapshot.runs.filter(run=>run.report_id&&run.status==='completed').map(run=><li key={run.report_id!}><button type="button" onClick={()=>void openReport(run.report_id!)}>{run.question} · 버전 {run.control_revision}</button><small>{run.run_id} / {run.report_id}</small></li>)}</ul>:<p className="muted">필수 품질 검사를 통과하고 제출된 문서만 여기에 표시됩니다.</p>}</section>}
   <section className="event-log">
    <div className="section-heading">
     <h2>{mode==='assets'?'로컬 장면 기록':'최근 서버 사건'}</h2>
     <span className="muted">{mode==='assets'?'LOCAL SYNTHETIC · KST':'불변 사건 · 한국 시간(KST) · 선택해서 입출력 확인'}</span>
    </div>
    {(mode==='assets'?artEvents:recent).length ? <ol>
     {[...(mode==='assets'?artEvents:recent)].sort((a,b)=>mode==='assets'?0:b.seq-a.seq).map(event=><li key={event.event_id}>
      <button type="button" className={detail?.event_id===event.event_id?'selected':''}
       onClick={()=>mode==='assets'?chooseArt(event as EventDetail):void chooseEvent(event)}>
       <time dateTime={event.occurred_at} title={`UTC 원본: ${event.occurred_at}`}>{formatEventTime(event.occurred_at)}</time>
       <span>{event.actor}</span><strong>{event.summary}
        {mode==='control'&&<small className="event-context">{currentEventIds.has(event.event_id)?'현재':'이전 실행/버전'} · v{event.control_revision}</small>}
       </strong><span className="event-state">{event.status}</span>
      </button>
     </li>)}
    </ol> : <p className="muted">{mode==='assets'?'위 동작 버튼을 누르면 입력·출력·시각이 있는 장면 기록이 쌓입니다.':'서버에 기록된 사건이 아직 없습니다.'}</p>}
    {mode==='control'&&snapshot?.events_has_more&&<p className="muted">최근 100개 사건만 표시합니다. 이전 기록은 서버 DB에 보존되어 있습니다.</p>}
   </section>
   <footer>픽셀 에셋: Kenney Tiny Town · Roguelike Characters · CC0 <a href="/에셋/에셋_목록.json" target="_blank" rel="noreferrer">출처와 파일 정보</a></footer>
  </main>
 </>;
}
