import {useEffect,useMemo,useReducer,useRef,useState} from 'react';
import type {EventDetail,JournalEvent,ReleaseOutcome,Report} from './계약';
import {api,sendCommand} from './통신';
import {useJournal} from './실행기록';
import {currentRunEvents,admittedDraft,formatEventTime} from './상태';
import {initialUI,uiReducer} from './창상태';
import type {Menu,Popup} from './창상태';
import {actors,names} from './마을배치';
import type {Actor} from './마을배치';
import type {VillageProps} from './장면계약';
import type {SceneAction} from './장면상태';
import {retryIdentity} from './장면상태';
import {actorState} from './역할투영';
import {Village} from './마을';
import {EventViewer,ReportViewer} from './상세보기';
const menus:Menu[]=['장면 도구','실행/큐','과거 기록','사건 기록','제어 도구','안내'];
function Icon({close=false}:{close?:boolean}){return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">{close?<path d="m6 6 12 12M18 6 6 18"/>:<path d="M4 6h16M4 12h16M4 18h16"/>}</svg>}
function localEvent(actor:Actor,nonce:number):EventDetail {return {event_id:`art-demo-${actor}-${nonce}`,run_id:'art-demo',control_revision:0,seq:nonce,occurred_at:new Date().toISOString(),type:'art-demo.work',actor,recipient:actor==='editor'?'publisher':'editor',status:'completed',summary:`${names[actor]}의 장면 실험용 작업 예시`,input:{prompt:'합성 자료의 관찰과 근거를 구분하여 짧게 정리하세요.',instruction:'마을 연구 활동을 시연합니다.',source:{title:'합성 실험 문서',text:'이 자료는 화면 동작을 확인하기 위해 만든 예시이며 실제 연구 근거가 아닙니다.'}},output:{summary:'합성 문서의 목적은 장면과 사건 상세 연결을 확인하는 것입니다.',actual_model_requests:0},provider:'art-demo',error:null};}
export function App(){
 const [ui,dispatch]=useReducer(uiReducer,{...initialUI,mode:location.hash==='#/control'?'control':'assets'});
 const {snapshot,refresh,connection,error:journalError}=useJournal();
 const [demo,setDemo]=useState<{action:SceneAction;nonce:number}>({action:'reset',nonce:0});
 const [detail,setDetail]=useState<EventDetail|null>(null),[report,setReport]=useState<Report|null>(null);
 const [latestReport,setLatestReport]=useState<Report|null>(null);
 const [loading,setLoading]=useState(false),[popupError,setPopupError]=useState('');
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[commandError,setCommandError]=useState('');
 const selectionToken=useRef(0),pending=useRef(false),retry=useRef<{text:string;id:string}|null>(null);
 const draftRef=useRef(ui.draft);draftRef.current=ui.draft;
 const menuRef=useRef<HTMLButtonElement>(null),closeRef=useRef<HTMLButtonElement>(null);
 const current=snapshot?.runs.find(r=>r.run_id===snapshot.current_run_id);
 const events=useMemo(()=>currentRunEvents(snapshot?.events??[],current),[snapshot,current]);
 const presentations=useMemo(()=>Object.fromEntries(actors.map(actor=>{const s=actorState(actor,snapshot,current);return [actor,{status:s.status,eventId:s.event?.event_id,summary:s.summary}];})) as VillageProps['actors'],[snapshot,current]);
 const codeEvent=events.filter(e=>!e.type.startsWith('model.')&&e.type!=='result.superseded').sort((a,b)=>b.seq-a.seq)[0];
 const codeActor=codeEvent?(actors.includes(codeEvent.actor as Actor)?codeEvent.actor as Actor:actors.includes(codeEvent.recipient as Actor)?codeEvent.recipient as Actor:'planner'):undefined;
 const completed=snapshot?.runs.filter(r=>r.status==='completed'&&r.report_id).sort((a,b)=>b.updated_at.localeCompare(a.updated_at))[0];
 useEffect(()=>{let disposed=false;setLatestReport(null);if(completed?.report_id)api.report(completed.report_id).then(r=>{if(!disposed)setLatestReport(r);}).catch(()=>{});return()=>{disposed=true;};},[completed?.report_id]);
 function close(){selectionToken.current++;dispatch({type:'close'});setLoading(false);setPopupError('');menuRef.current?.focus();}
 function open(popup:Popup){selectionToken.current++;setDetail(null);setReport(null);setLoading(false);setPopupError('');dispatch({type:'open',popup});}
 useEffect(()=>{const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')close();};window.addEventListener('keydown',handler);const hash=()=>dispatch({type:'mode',mode:location.hash==='#/control'?'control':'assets'});window.addEventListener('hashchange',hash);return()=>{window.removeEventListener('keydown',handler);window.removeEventListener('hashchange',hash);};},[]);
 useEffect(()=>{if(ui.popup)closeRef.current?.focus();},[ui.popup?.kind]);
 async function showEvent(id:string,local?:EventDetail){open({kind:'event',id,local:!!local});const token=selectionToken.current;if(local){setDetail(local);return;}setLoading(true);try{const result=await api.detail(id);if(token===selectionToken.current)setDetail(result);}catch(e){if(token===selectionToken.current)setPopupError(String(e));}finally{if(token===selectionToken.current)setLoading(false);}}
 async function showReport(id:string){open({kind:'report',id});const token=selectionToken.current;setLoading(true);try{const result=await api.report(id);if(token===selectionToken.current)setReport(result);}catch(e){if(token===selectionToken.current)setPopupError(String(e));}finally{if(token===selectionToken.current)setLoading(false);}}
 function actorClick(actor:Actor){if(ui.mode==='assets'){const local=localEvent(actor,demo.nonce);void showEvent(local.event_id,local);}else{const id=presentations[actor].eventId;if(id)void showEvent(id);else open({kind:'idle',actor});}}
 async function submit(){if(pending.current||!ui.draft.trim())return;if(ui.mode==='assets'){setNotice('장면 실험에서는 전송하지 않습니다. 메뉴에서 실행 제어로 바꿔 주세요.');return;}const request=retryIdentity(retry.current,ui.draft,()=>crypto.randomUUID());retry.current=request;pending.current=true;setBusy(true);setCommandError('');try{const admitted=await sendCommand(request.text,request.id);retry.current=null;dispatch({type:'draft',text:admittedDraft(draftRef.current,request.text)});setNotice(admitted.status==='applied'?'지시가 적용되었습니다.':admitted.status==='queued'?'큐에 접수되었습니다. 아직 실행된 것은 아닙니다.':'지시가 접수되었습니다. 적용 여부는 사건 기록에서 확인하세요.');await refresh().catch(e=>setNotice(`지시는 접수되었지만 최신 상태 조회에 실패했습니다: ${String(e)}. 메뉴의 실행/큐에서 확인해 주세요.`));}catch(e){setCommandError(`${String(e)} · 입력은 보존됩니다. 같은 내용으로 재시도하면 같은 요청 ID를 사용합니다.`);}finally{pending.current=false;setBusy(false);}}
 const activeGates=snapshot?.waiting.filter(g=>!g.is_stale&&g.run_id===current?.run_id&&g.control_revision===current?.control_revision)??[];
 const staleGates=snapshot?.waiting.filter(g=>g.is_stale)??[];
 async function operate(action:()=>Promise<unknown>){if(pending.current)return;pending.current=true;setBusy(true);setPopupError('');try{await action();await refresh();}catch(e){setPopupError(`${String(e)} · 상태를 확인하고 다시 시도하세요.`);}finally{pending.current=false;setBusy(false);}}
 const release=(kind:'one'|'all'|'stale',outcome:ReleaseOutcome)=>{const targets=kind==='stale'?staleGates:activeGates;const ids=(kind==='one'?targets.slice(0,1):targets).map(g=>g.gate_id);if(ids.length)void operate(()=>api.release(ids,outcome));};
 function eventList(list:JournalEvent[]){return list.length?<ol className="record-list">{[...list].sort((a,b)=>b.seq-a.seq).map(e=><li key={e.event_id}><button onClick={()=>void showEvent(e.event_id)}><span>{e.summary||e.type}</span><small>{e.actor} · {formatEventTime(e.occurred_at)} KST · {e.event_id}</small></button></li>)}</ol>:<p>아직 기록된 사건이 없습니다.</p>;}
 const historyId=ui.popup?.kind==='history'?ui.popup.runId:null;
 const history=historyId?snapshot?.runs.find(r=>r.run_id===historyId):undefined;
 const title=ui.popup?.kind==='event'?'사건 상세':ui.popup?.kind==='report'?'최종 보고서':ui.popup?.kind==='history'?'실행 상세':ui.popup?.kind==='idle'?'역할 상태':ui.popup?.kind;
 return <main className="village-app">
  <Village mode={ui.mode} demo={demo} actors={presentations} code={codeEvent&&codeActor?{eventId:codeEvent.event_id,actor:codeActor}:undefined} latestReport={latestReport?{id:latestReport.report_id,title:latestReport.title,createdAt:latestReport.created_at}:undefined} onActorClick={actorClick} onCodeClick={()=>{if(ui.mode==='assets'){const e=localEvent('planner',demo.nonce);e.type='art-demo.assignment';e.provider='art-demo';e.input={instruction:'합성 조사 분담을 코드로 배정합니다.'};e.output={assignments:['조사관 1: 자료 확인','조사관 2: 근거 비교'],actual_model_requests:0};void showEvent(e.event_id,e);}else if(codeEvent)void showEvent(codeEvent.event_id);}} onPaperClick={()=>{if(latestReport)void showReport(latestReport.report_id);else open({kind:'안내'});}}/>
  <button ref={menuRef} className="menu-toggle icon-button" data-testid="menu-toggle" aria-label="메뉴 열기" aria-expanded={ui.drawer} onClick={()=>dispatch({type:'drawer'})}><Icon/></button>
  <span className="mock-badge">모의 응답</span>
  {ui.drawer&&<nav className="drawer" data-testid="drawer" aria-label="마을 메뉴"><h2>연구 마을</h2><div className="mode-options"><button aria-pressed={ui.mode==='assets'} onClick={()=>{location.hash='/assets';dispatch({type:'mode',mode:'assets'});}}>에셋 실험</button><button aria-pressed={ui.mode==='control'} onClick={()=>{location.hash='/control';dispatch({type:'mode',mode:'control'});}}>실행 제어</button></div>{menus.map(menu=><button key={menu} onClick={()=>open({kind:menu})}>{menu}</button>)}<p>테스트 대역 · 실제 모델 요청 0</p></nav>}
  {ui.popup&&<section className="popup" data-testid="popup" role="dialog" aria-modal="false" aria-labelledby="popup-title"><header className="popup-header"><h2 id="popup-title">{title}</h2><button ref={closeRef} className="icon-button" data-testid="popup-close" aria-label="닫기" onClick={close}><Icon close/></button></header><div className="popup-body">
   {loading&&<p role="status">보존된 기록을 불러오는 중입니다.</p>}
   {popupError&&<p role="alert">{popupError} <button onClick={()=>{if(ui.popup?.kind==='event')void showEvent(ui.popup.id);else if(ui.popup?.kind==='report')void showReport(ui.popup.id);else void operate(refresh);}}>다시 조회</button></p>}
   {ui.popup.kind==='event'&&detail&&<EventViewer event={detail}/>}
   {ui.popup.kind==='report'&&report&&<ReportViewer report={report}/>}
   {ui.popup.kind==='idle'&&<p>{names[ui.popup.actor as Actor]}에게 연결된 현재 실행 사건이 없습니다. 과거 사건은 사건 기록에서 확인할 수 있습니다.</p>}
   {ui.popup.kind==='장면 도구'&&<><p>장면 연출만 바꿉니다. 실행·큐·서버 기록은 변경하지 않습니다.</p><div className="button-group">{([['이동 보기','move'],['작업 보기','work'],['문서 운반','deliver'],['처음으로','reset']] as const).map(([label,action])=><button key={action} onClick={()=>setDemo(d=>({action,nonce:d.nonce+1}))}>{label}</button>)}</div>{ui.mode==='control'&&<p>연출을 보려면 메뉴에서 에셋 실험으로 바꿔 주세요.</p>}</>}
   {ui.popup.kind==='실행/큐'&&<><p className="notice">{connection} · 실제 모델 요청 0</p>{current?<><h3>현재 실행</h3><dl><dt>질문</dt><dd>{current.question}</dd><dt>실행 ID</dt><dd>{current.run_id}</dd><dt>상태 / 단계 / 버전</dt><dd>{current.status} / {current.phase} / {current.control_revision}</dd></dl><h3>조사 계획</h3>{current.plan.length?<ul>{current.plan.map(a=><li key={a.section_id}>{a.title} · {a.assignee}</li>)}</ul>:<p>아직 배정된 계획이 없습니다.</p>}</>:<p>현재 실행이 없습니다. 하단 입력으로 지시할 수 있습니다.</p>}<h3>대기 큐</h3>{snapshot?.queue.length?<ol>{snapshot.queue.map(q=><li key={q.instruction_id}>{q.text} · {q.status}</li>)}</ol>:<p>대기 중인 지시가 없습니다.</p>}<p>{snapshot?.queue_paused?`큐가 일시정지되었습니다: ${snapshot.queue_pause_reason}`:'큐가 일시정지되어 있지 않습니다.'}</p><button disabled={busy||!snapshot?.queue_paused} onClick={()=>void operate(api.continue)}>큐 계속</button></>}
   {ui.popup.kind==='과거 기록'&&<>{snapshot?.runs.length?<ol className="record-list">{[...snapshot.runs].reverse().map(run=><li key={run.run_id}><h3>{run.question}</h3><p>{run.status} · 버전 {run.control_revision} · {run.run_id}</p><button onClick={()=>open({kind:'history',runId:run.run_id})}>자세히 보기</button></li>)}</ol>:<p>보존된 실행이 없습니다.</p>}</>}
   {ui.popup.kind==='history'&&<><button onClick={()=>dispatch({type:'back'})}>목록으로</button>{history?<><h3>{history.question}</h3><dl><dt>실행 ID</dt><dd>{history.run_id}</dd><dt>상태 / 단계 / 버전</dt><dd>{history.status} / {history.phase} / {history.control_revision}</dd></dl><h3>초안</h3><pre>{history.draft??'작성된 초안이 없습니다.'}</pre><h3>품질 검사</h3><pre>{JSON.stringify(history.quality,null,2)}</pre>{history.report_id?<button onClick={()=>void showReport(history.report_id!)}>최종 보고서 보기</button>:<p>다운로드 가능한 최종 보고서는 아직 없습니다.</p>}<h3>실제 사건</h3>{eventList(snapshot?.events.filter(e=>e.run_id===history.run_id)??[])}</>:<p>해당 실행 기록을 찾지 못했습니다.</p>}</>}
   {ui.popup.kind==='사건 기록'&&<>{eventList(snapshot?.events??[])}{snapshot?.events_has_more&&<p>이 창은 최근 사건만 표시합니다. 더 오래된 사건은 서버 데이터베이스에 보존되어 있습니다.</p>}</>}
   {ui.popup.kind==='제어 도구'&&<><p>모의 응답의 명시적인 대기 ID를 선택해 처리합니다. 실제 모델은 호출하지 않습니다.</p><p>현재 대기 {activeGates.length}건 · 이전 응답 {staleGates.length}건</p><div className="button-group"><button disabled={busy||!activeGates.length} onClick={()=>release('one','success')}>현재 1건 완료</button><button disabled={busy||!activeGates.length} onClick={()=>release('all','success')}>현재 모두 완료</button><button disabled={busy||!staleGates.length} onClick={()=>release('stale','success')}>이전 응답 돌려주기</button><button disabled={busy||!activeGates.length} onClick={()=>release('one','failure')}>실패 응답</button><button disabled={busy||!activeGates.length} onClick={()=>release('all','quality_fail')}>불합격 초안</button><button className="danger" disabled={busy||current?.status!=='running'} onClick={()=>{if(current?.status==='running')void operate(()=>api.cancel(current.run_id,current.control_revision));}}>현재 실행 취소</button></div>{!activeGates.length&&<p>현재 실행 버전에 응답 대기 중인 작업이 없어 완료·실패 조작을 할 수 없습니다.</p>}{!staleGates.length&&<p>돌려줄 이전 응답이 없습니다.</p>}{current?.status!=='running'&&<p>진행 중인 실행이 없어 취소할 수 없습니다.</p>}</>}
   {ui.popup.kind==='안내'&&<><p>마을이 작업 화면입니다. 인물·말풍선은 고정된 사건의 상세를, 제출된 종이는 해당 최종 보고서를 엽니다.</p><p>에셋 실험은 합성 장면입니다. 실행 제어는 실제 로컬 API와 보존된 기록에 연결되며, 응답 제공자는 테스트 대역입니다. 실제 모델 요청은 0입니다.</p><p>일반 지시는 즉시 개입하며, /큐 지시는 대기 큐에 접수됩니다. 접수와 적용은 다릅니다. 실패·취소·품질 불합격 후 큐는 직접 계속해야 합니다.</p><p>창을 닫거나 Escape를 눌러도 입력과 실행은 유지됩니다.</p><p>{connection}</p></>}
  </div></section>}
  <div className="composer-area">{(notice||commandError||journalError)&&<div className="composer-notice" role={commandError||journalError?'alert':'status'}>{commandError||journalError||notice}{commandError&&<button disabled={busy} onClick={()=>void submit()}>전송 재시도</button>}<button className="notice-dismiss" aria-label="알림 닫기" onClick={()=>{setNotice('');setCommandError('');}}><Icon close/></button></div>}<form className="composer" onSubmit={e=>{e.preventDefault();void submit();}}><textarea data-testid="command-input" aria-label="연구 지시" placeholder={ui.mode==='control'?'연구 지시를 입력하세요. /큐 로 대기열에 추가':'연구 지시 · 실행은 메뉴의 실행 제어에서'} value={ui.draft} onChange={e=>dispatch({type:'draft',text:e.target.value})} rows={2}/><button data-testid="command-submit" className="primary" type="submit" disabled={busy||!ui.draft.trim()}>{busy?'확인 중':'지시 보내기'}</button></form></div>
 </main>;
}
