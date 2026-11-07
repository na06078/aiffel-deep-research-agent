import {useEffect,useMemo,useReducer,useRef,useState} from 'react';
import type {EventDetail,JournalEvent,ReleaseOutcome,Report,RunSnapshot} from './계약';
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
import type {RetryCommand} from './장면상태';
import {commandSource,confirmedSource,sourceLabel} from './자료모드';
import type {SourceSelection} from './자료모드';
import {actorState} from './역할투영';
import {Village} from './마을';
import {EventViewer,ReportViewer} from './상세보기';
import {useRecordedReplay} from './기록재생연결';
import {projectionSnapshot,sceneClock} from './시간재생';
import {Timeline} from './실행타임라인';
import {PastRecordList} from './과거기록목록';
// 실행삭제.ts(논리)와 실행삭제.tsx(화면)는 이름이 같아 확장자를 붙여 가리킨다.
import {DeleteButton,DeleteConfirm,useRunDelete} from './실행삭제.tsx';
import {armedFlowFor,visibleEvents} from './실행삭제.ts';
import {RunFailureNotice} from './실행실패';
import {useDrawerHeight} from './드로어크기';
import type {CSSProperties} from 'react';
const menus:Menu[]=['장면 도구','실행/큐','과거 기록','사건 기록','제어 도구','안내'];
const attemptsOf=(n:number,limit?:number)=>limit===undefined?`${n}`:`${n} / ${limit}`; // 상한은 서버가 보낸 기록에만 있다.
function WebUsageView({run}:{run:RunSnapshot}){return run.web_usage?<><p>{`논리 검색 시도 ${attemptsOf(run.web_usage.search_attempts,run.web_usage.search_limit)} · 본문 수집 시도 ${attemptsOf(run.web_usage.fetch_attempts,run.web_usage.fetch_limit)}`}</p><p>{`관측 HTTP 요청 ${run.web_usage.observed_http_requests} · 실제 모델 요청 ${run.actual_model_requests}`}</p><p className="notice">HTTP는 Python 전송 경계의 관측입니다. 네이티브 내부 redirect·원격 처리까지 센 값이 아니며 논리 도구 시도·모델 요청과 다릅니다.</p></>:null;}
function Icon({close=false}:{close?:boolean}){return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">{close?<path d="m6 6 12 12M18 6 6 18"/>:<path d="M4 6h16M4 12h16M4 18h16"/>}</svg>}
function localEvent(actor:Actor,nonce:number):EventDetail {return {event_id:`art-demo-${actor}-${nonce}`,run_id:'art-demo',control_revision:0,seq:nonce,occurred_at:new Date().toISOString(),type:'art-demo.work',actor,recipient:actor==='editor'?'publisher':'editor',status:'completed',summary:`${names[actor]}의 장면 실험용 작업 예시`,input:{prompt:'합성 자료의 관찰과 근거를 구분하여 짧게 정리하세요.',instruction:'마을 연구 활동을 시연합니다.',source:{title:'합성 실험 문서',text:'이 자료는 화면 동작을 확인하기 위해 만든 예시이며 실제 연구 근거가 아닙니다.'}},output:{summary:'합성 문서의 목적은 장면과 사건 상세 연결을 확인하는 것입니다.',actual_model_requests:0},provider:'art-demo',error:null};}
export function App(){
 const {snapshot,refresh,connection,error:journalError}=useJournal();
 const [ui,dispatch]=useReducer(uiReducer,{...initialUI,mode:location.hash==='#/control'||(!location.hash&&snapshot?.mode==='research')?'control':'assets'});
 useEffect(()=>{if(!location.hash&&snapshot?.mode==='research')dispatch({type:'mode',mode:'control'});},[snapshot?.mode]);
 const [sourceAck,setSourceAck]=useState<SourceSelection|null>(null);
 const sourceSelection=useRef<SourceSelection|null>(null);
 sourceSelection.current=snapshot?confirmedSource(sourceSelection.current??sourceAck,snapshot):sourceSelection.current??sourceAck;
 const playback=useRecordedReplay(snapshot);
 const replaying=playback.replay;
 const [demo,setDemo]=useState<{action:SceneAction;nonce:number}>({action:'reset',nonce:0});
 const [detail,setDetail]=useState<EventDetail|null>(null),[report,setReport]=useState<Report|null>(null);
 const [latestReport,setLatestReport]=useState<Report|null>(null);
 const [loading,setLoading]=useState(false),[popupError,setPopupError]=useState('');
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[commandError,setCommandError]=useState('');
 const selectionToken=useRef(0),pending=useRef(false),retry=useRef<RetryCommand|null>(null);
 // 삭제 성공 뒤 재생 복귀·창 복귀·알림은 앱 상태이므로 콜백으로 연결한다(논리는 실행삭제.ts).
 const del=useRunDelete({replayRunId:replaying?playback.runId:null,popup:ui.popup,refresh,leaveReplay:playback.live,onDeletedRun:playback.deleted,returnToList:()=>open({kind:'과거 기록'}),notify:setNotice});
 const draftRef=useRef(ui.draft);draftRef.current=ui.draft;
 const menuRef=useRef<HTMLButtonElement>(null),closeRef=useRef<HTMLButtonElement>(null);
 const surfaceRef=useRef<HTMLElement>(null),composerRef=useRef<HTMLDivElement>(null);
 const drawerSize=useDrawerHeight(surfaceRef,composerRef,!!ui.popup);
 const selectedReportId=ui.popup?.kind==='report'?ui.popup.id:null;
 const selectedReportAllowed=selectedReportId===null||!replaying||playback.projection?.report?.report_id===selectedReportId;
 useEffect(()=>{
  if(selectedReportId===null||selectedReportAllowed)return;
  // 재생 시점에서 사라진 정본의 창과 진행 중 조회를 함께 해제한다. 타임라인 포커스는 유지한다.
  selectionToken.current++;setReport(null);setLoading(false);setPopupError('');dispatch({type:'close'});
 },[selectedReportId,selectedReportAllowed]);
 useEffect(()=>{
  const surface=surfaceRef.current,composer=composerRef.current;
  if(!surface||!composer)return;
  const measure=()=>{
   const popup=surface.querySelector<HTMLElement>('.popup');
   if(!popup)return;
   // 알림/오류와 재생 배너의 실제 높이만큼 팝업 읽기 공간을 확보한다.
   const available=composer.getBoundingClientRect().top-popup.getBoundingClientRect().top-12;
   surface.style.setProperty('--popup-available-height',`${Math.max(64,available)}px`);
  };
  const observer=new ResizeObserver(measure);observer.observe(composer);
  const frame=requestAnimationFrame(measure);window.addEventListener('resize',measure);
  return()=>{observer.disconnect();cancelAnimationFrame(frame);window.removeEventListener('resize',measure);};
 },[ui.popup?.kind,playback.open,replaying,drawerSize.height]);
 const current=snapshot?.runs.find(r=>r.run_id===snapshot.current_run_id);
 const visibleCurrent=replaying?playback.projection?.run:current;
 const visibleSnapshot=useMemo(()=>replaying?(playback.projection?projectionSnapshot(playback.projection):null):snapshot,[replaying,playback.projection,snapshot]);
 // An ack can be newer than a GET that still describes the run before it ended.
 const currentSourceConfirmed=!!snapshot&&snapshot.high_watermark_seq>=(sourceSelection.current?.seq??0);
 const indicatorSource=replaying?(visibleCurrent?.phase==='not_started'?undefined:visibleCurrent?.source_mode):current?.status==='running'&&currentSourceConfirmed?(current.source_mode??'local'):sourceSelection.current?.source_mode;
 const actual=visibleSnapshot?.provider==='openai';
 const requestLabel=`${visibleSnapshot?(actual?'OpenAI':'테스트 대역'):'제공자 조회 전'} · 실제 모델 요청 ${visibleSnapshot?.actual_model_requests??0}`;
 const recordedEvents=replaying?playback.projection?.events??[]:playback.archive?.events??visibleSnapshot?.events??[];
 const events=useMemo(()=>visibleEvents(currentRunEvents(recordedEvents,visibleCurrent)),[recordedEvents,visibleCurrent]);
 const presentations=useMemo(()=>Object.fromEntries(actors.map(actor=>{const s=actorState(actor,visibleSnapshot,visibleCurrent);return [actor,{status:s.status,eventId:s.event?.event_id,summary:s.summary}];})) as VillageProps['actors'],[visibleSnapshot,visibleCurrent]);
 const codeEvent=events.filter(e=>!e.type.startsWith('model.')&&e.type!=='result.superseded').sort((a,b)=>b.seq-a.seq)[0];
 const codeActor=codeEvent?(actors.includes(codeEvent.actor as Actor)?codeEvent.actor as Actor:actors.includes(codeEvent.recipient as Actor)?codeEvent.recipient as Actor:'planner'):undefined;
 const completed=snapshot?.runs.filter(r=>r.status==='completed'&&r.report_id).sort((a,b)=>b.updated_at.localeCompare(a.updated_at))[0];
 useEffect(()=>{let disposed=false;setLatestReport(null);if(completed?.report_id)api.report(completed.report_id).then(r=>{if(!disposed)setLatestReport(r);}).catch(()=>{});return()=>{disposed=true;};},[completed?.report_id]);
 function close(){del.cancel();selectionToken.current++;dispatch({type:'close'});setLoading(false);setPopupError('');menuRef.current?.focus();}
 function open(popup:Popup){del.cancel();selectionToken.current++;setDetail(null);setReport(null);setLoading(false);setPopupError('');dispatch({type:'open',popup});}
 useEffect(()=>{const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')close();};window.addEventListener('keydown',handler);const hash=()=>dispatch({type:'mode',mode:location.hash==='#/control'?'control':'assets'});window.addEventListener('hashchange',hash);return()=>{window.removeEventListener('keydown',handler);window.removeEventListener('hashchange',hash);};},[]);
 useEffect(()=>{if(ui.popup)closeRef.current?.focus();},[ui.popup?.kind]);
 async function showEvent(id:string,local?:EventDetail){local??=playback.archive?.events.find(e=>e.event_id===id);open({kind:'event',id,local:!!local});const token=selectionToken.current;if(local){setDetail(local);return;}setLoading(true);try{const result=await api.detail(id);if(token===selectionToken.current)setDetail(result);}catch(e){if(token===selectionToken.current)setPopupError(String(e));}finally{if(token===selectionToken.current)setLoading(false);}}
 async function showReport(id:string){open({kind:'report',id});const token=selectionToken.current;setLoading(true);try{const result=await api.report(id);if(token===selectionToken.current)setReport(result);}catch(e){if(token===selectionToken.current)setPopupError(String(e));}finally{if(token===selectionToken.current)setLoading(false);}}
 function actorClick(actor:Actor){if(ui.mode==='assets'&&!replaying){const local=localEvent(actor,demo.nonce);void showEvent(local.event_id,local);}else{const id=presentations[actor].eventId;if(id)void showEvent(id);else open({kind:'idle',actor});}}
 async function submit(){if(replaying){setCommandError('읽기 전용 기록 재생 중에는 지시를 전송하지 않습니다. 실시간으로 복귀해 주세요.');return;}if(pending.current||!ui.draft.trim())return;if(ui.mode==='assets'){setNotice('장면 실험에서는 전송하지 않습니다. 주소 끝을 #/control로 바꿔 실행 제어로 들어가 주세요.');return;}const request=retryIdentity(retry.current,ui.draft,()=>crypto.randomUUID(),commandSource(ui.draft,sourceSelection.current?.source_mode));retry.current=request;pending.current=true;setBusy(true);setCommandError('');try{const admitted=await sendCommand(request.text,request.id,request.source_mode);sourceSelection.current=confirmedSource(sourceSelection.current,admitted);setSourceAck(sourceSelection.current);retry.current=null;dispatch({type:'draft',text:admittedDraft(draftRef.current,request.text)});setNotice(admitted.kind==='mode_change'?(admitted.state_seq!==undefined&&sourceSelection.current&&admitted.state_seq<sourceSelection.current.seq?`과거 응답의 자료 선택입니다. 최신 확인 선택 ${sourceLabel(sourceSelection.current.source_mode)}을 유지합니다. 새 선택을 원하면 자료 명령을 다시 보내세요. 연구를 시작하거나 완료한 것이 아닙니다.`:`${sourceLabel(admitted.source_mode??'local')} 자료 선택을 저장했습니다. 연구를 시작하거나 완료한 것이 아닙니다.`):admitted.status==='applied'?'지시가 적용되었습니다.':admitted.status==='queued'?'큐에 접수되었습니다. 아직 실행된 것은 아닙니다.':'지시가 접수되었습니다. 적용 여부는 사건 기록에서 확인하세요.');await refresh().catch(e=>setNotice(`지시는 접수되었지만 최신 상태 조회에 실패했습니다: ${String(e)}. 메뉴의 실행/큐에서 확인해 주세요.`));}catch(e){setCommandError(`${String(e)} · 입력은 보존됩니다. ${String(e).includes('source_mode_locked')?'진행 중인 연구의 자료는 고정됩니다. /큐 와 자료 명령 뒤에 질문을 넣어 예약하거나, 현재 연구 종료 후 다음 연구에서 자료를 선택하세요.':'같은 내용으로 재시도하면 최초 자료와 같은 요청 ID를 사용합니다.'}`);}finally{pending.current=false;setBusy(false);}}
 const activeGates=snapshot?.waiting.filter(g=>!g.is_stale&&g.run_id===current?.run_id&&g.control_revision===current?.control_revision)??[];
 const staleGates=snapshot?.waiting.filter(g=>g.is_stale)??[];
 async function operate(action:()=>Promise<unknown>){if(replaying){setPopupError('읽기 전용 기록 재생 중에는 실행·큐를 변경할 수 없습니다. 실시간으로 복귀해 주세요.');return;}if(pending.current)return;pending.current=true;setBusy(true);setPopupError('');try{await action();await refresh();}catch(e){setPopupError(`${String(e)} · 상태를 확인하고 다시 시도하세요.`);}finally{pending.current=false;setBusy(false);}}
 const release=(kind:'one'|'all'|'stale',outcome:ReleaseOutcome)=>{const targets=kind==='stale'?staleGates:activeGates;const ids=(kind==='one'?targets.slice(0,1):targets).map(g=>g.gate_id);if(ids.length)void operate(()=>api.release(ids,outcome));};
 function eventList(all:JournalEvent[]){const list=visibleEvents(all);return list.length?<ol className="record-list">{[...list].sort((a,b)=>b.seq-a.seq).map(e=><li key={e.event_id}><button onClick={()=>void showEvent(e.event_id)}><span>{e.summary||e.type}</span><small>{e.actor} · {formatEventTime(e.occurred_at)} KST · {e.event_id}</small></button></li>)}</ol>:<p>아직 기록된 사건이 없습니다.</p>;}
 const historyId=ui.popup?.kind==='history'?ui.popup.runId:null;
 const history=historyId?snapshot?.runs.find(r=>r.run_id===historyId):undefined;
 const title=ui.popup?.kind==='event'?'사건 상세':ui.popup?.kind==='report'?'최종 보고서':ui.popup?.kind==='history'?'실행 상세':ui.popup?.kind==='idle'?'역할 상태':ui.popup?.kind;
 const visibleReport=replaying?playback.projection?.report??null:latestReport;
 const clock=playback.archive&&(replaying||ui.mode==='control')?sceneClock(playback.archive,playback.at,replaying,visibleReport):undefined;
 // 재생 중 옛 실행 알림은 숨기지만, 방금 다른 기록을 삭제한 결과·정리 경고는 보여야 한다.
 const showNotice=!!notice&&(!replaying||notice.startsWith('실행을 삭제했습니다.'));
 return <main ref={surfaceRef} style={{'--timeline-height':`${playback.open?drawerSize.height:0}px`} as CSSProperties} className={`village-app${playback.open?' timeline-open':''}`}>
  <Village mode={replaying?'control':ui.mode} demo={demo} actors={presentations} code={codeEvent&&codeActor?{eventId:codeEvent.event_id,actor:codeActor}:undefined} clock={clock} latestReport={visibleReport?{id:visibleReport.report_id,title:visibleReport.title,createdAt:visibleReport.created_at}:undefined} onActorClick={actorClick} onCodeClick={()=>{if(ui.mode==='assets'&&!replaying){const e=localEvent('planner',demo.nonce);e.type='art-demo.assignment';e.provider='art-demo';e.input={instruction:'합성 조사 분담을 코드로 배정합니다.'};e.output={assignments:['조사관 1: 자료 확인','조사관 2: 근거 비교'],actual_model_requests:0};void showEvent(e.event_id,e);}else if(codeEvent)void showEvent(codeEvent.event_id);}} onPaperClick={()=>{if(visibleReport)void showReport(visibleReport.report_id);else open({kind:'안내'});}}/>
  <button ref={menuRef} className="menu-toggle icon-button" data-testid="menu-toggle" aria-label="메뉴 열기" aria-expanded={ui.drawer} onClick={()=>dispatch({type:'drawer'})}><Icon/></button>
  <span className="source-mode-indicator" data-testid="source-mode-indicator" role="status" aria-live="polite" aria-label="현재 자료 모드">{sourceLabel(indicatorSource)}</span>
  {((!replaying&&ui.mode==='assets')||(!actual&&visibleSnapshot))&&<span className="mock-badge">{!replaying&&ui.mode==='assets'?'합성 장면 · 모의 응답':'모의 응답'}</span>}
  {ui.drawer&&<nav className="drawer" data-testid="drawer" aria-label="마을 메뉴"><h2>연구 마을</h2>{menus.map(menu=><button key={menu} onClick={()=>open({kind:menu})}>{menu}</button>)}</nav>}
  {ui.popup&&<section className="popup" data-testid="popup" role="dialog" aria-modal="false" aria-labelledby="popup-title"><header className="popup-header"><h2 id="popup-title">{title}</h2><button ref={closeRef} className="icon-button" data-testid="popup-close" aria-label="닫기" onClick={close}><Icon close/></button></header><div className="popup-body">{replaying&&<p className="notice">읽기 전용 기록 재생 중입니다. 실행·큐 변경은 실시간 복귀 후에만 가능합니다. <button onClick={()=>{playback.live();void refresh().catch(()=>{});}}>실시간 복귀</button></p>}
   {loading&&<p role="status">보존된 기록을 불러오는 중입니다.</p>}
   {popupError&&<p role="alert">{popupError} <button onClick={()=>{if(ui.popup?.kind==='event')void showEvent(ui.popup.id);else if(ui.popup?.kind==='report')void showReport(ui.popup.id);else void operate(refresh);}}>다시 조회</button></p>}
   {ui.popup.kind==='event'&&detail&&<EventViewer event={detail}/>}
   {ui.popup.kind==='report'&&selectedReportAllowed&&report&&<ReportViewer report={report} quality={snapshot?.runs.find(run=>run.report_id===report.report_id)?.quality}/>}
   {ui.popup.kind==='idle'&&<p>{names[ui.popup.actor as Actor]}에게 연결된 현재 실행 사건이 없습니다. 과거 사건은 사건 기록에서 확인할 수 있습니다.</p>}
   {ui.popup.kind==='장면 도구'&&<><p>장면 연출만 바꿉니다. 실행·큐·서버 기록은 변경하지 않습니다.</p><div className="button-group">{([['이동 보기','move'],['작업 보기','work'],['문서 운반','deliver'],['처음으로','reset']] as const).map(([label,action])=><button key={action} onClick={()=>setDemo(d=>({action,nonce:d.nonce+1}))}>{label}</button>)}</div>{ui.mode==='control'&&<p>연출을 보려면 주소 끝을 #/assets로 바꿔 에셋 실험으로 들어가 주세요.</p>}</>}
   {ui.popup.kind==='실행/큐'&&<><p className="notice">{connection} · {requestLabel} · 연결 상태는 생성 성공을 뜻하지 않습니다.</p>{visibleSnapshot?.budget&&<><h3>호출 예산</h3><p>{`시도 ${visibleSnapshot.budget.attempt_count} / ${visibleSnapshot.budget.max_attempts} · 남은 시도 ${visibleSnapshot.budget.remaining_attempts}`}</p><p>{`사용 추정 $${visibleSnapshot.budget.spent_usd.toFixed(4)} · 예약 $${visibleSnapshot.budget.reserved_usd.toFixed(4)} · 한도 $${visibleSnapshot.budget.max_usd.toFixed(4)}`}</p><p>{`토큰 입력 ${visibleSnapshot.budget.input_tokens} · 출력 ${visibleSnapshot.budget.output_tokens} · 비용 불명 ${visibleSnapshot.budget.unknown_count}`}</p><p>정상요금 기준 추정치이며 실제 청구·계정 무료 혜택을 확인한 값이 아닙니다.</p></>}{visibleCurrent?<><h3>현재 실행</h3><dl><dt>질문</dt><dd>{visibleCurrent.question}</dd><dt>자료</dt><dd>{sourceLabel(replaying&&visibleCurrent.phase==='not_started'?undefined:visibleCurrent.source_mode??'local')}</dd><dt>실행 ID</dt><dd>{visibleCurrent.run_id}</dd><dt>상태 / 단계 / 버전</dt><dd>{visibleCurrent.status} / {visibleCurrent.phase} / {visibleCurrent.control_revision}</dd></dl><p>현재 실행의 실제 모델 요청 {visibleCurrent.actual_model_requests}</p><WebUsageView run={visibleCurrent}/><h3>품질 검사 / 종료 사유</h3><RunFailureNotice run={visibleCurrent}/><p>{visibleCurrent.quality.passed===null?'아직 판정되지 않았습니다.':visibleCurrent.quality.passed?'필수 검사 통과':'필수 검사 불합격'}</p><ul>{visibleCurrent.quality.reasons.map((reason,i)=><li key={i}>{reason}</li>)}</ul>{eventList(events.filter(e=>e.status==='failed'||e.type.endsWith('.failed')))}<button className="danger" disabled={replaying||busy||visibleCurrent.status!=='running'} onClick={()=>void operate(()=>api.cancel(visibleCurrent.run_id,visibleCurrent.control_revision))}>현재 실행 취소</button><h3>조사 계획</h3>{visibleCurrent.plan.length?<ul>{visibleCurrent.plan.map(a=><li key={a.section_id}>{a.title} · {a.assignee}</li>)}</ul>:<p>아직 배정된 계획이 없습니다.</p>}</>:<p>현재 실행이 없습니다. 하단 입력으로 지시할 수 있습니다.</p>}<h3>대기 큐</h3>{visibleSnapshot?.queue.length?<ol>{visibleSnapshot.queue.map(q=><li key={q.instruction_id}>{q.raw_text??q.text} · {sourceLabel(q.source_mode??'local')} · {q.status}</li>)}</ol>:<p>대기 중인 지시가 없습니다.</p>}<p>{visibleSnapshot?.queue_paused?`큐가 일시정지되었습니다: ${visibleSnapshot.queue_pause_reason}`:'큐가 일시정지되어 있지 않습니다.'}</p><button disabled={replaying||busy||!visibleSnapshot?.queue_paused} onClick={()=>void operate(api.continue)}>큐 계속</button></>}
   {ui.popup.kind==='과거 기록'&&<PastRecordList runs={snapshot?.runs??[]} currentRunId={snapshot?.current_run_id??null} replayRunId={replaying?playback.runId:null} onDetail={runId=>open({kind:'history',runId})} onReplay={runId=>{playback.select(runId);close();}} deleteFlow={del.flow} onDeleteStart={del.start} onDeleteConfirm={()=>void del.confirm()} onDeleteCancel={del.cancel}/>}
   {ui.popup.kind==='history'&&<><button onClick={()=>dispatch({type:'back'})}>목록으로</button>{history?<><h3>{history.question}</h3><WebUsageView run={history}/><dl><dt>자료</dt><dd>{sourceLabel(history.source_mode??'local')}</dd><dt>실행 ID</dt><dd>{history.run_id}</dd><dt>상태 / 단계 / 버전</dt><dd>{history.status} / {history.phase} / {history.control_revision}</dd></dl><RunFailureNotice run={history}/><h3>초안</h3><pre>{history.draft??'작성된 초안이 없습니다.'}</pre><h3>품질 검사</h3><pre>{JSON.stringify(history.quality,null,2)}</pre>{history.report_id?<button onClick={()=>void showReport(history.report_id!)}>최종 보고서 보기</button>:<p>다운로드 가능한 최종 보고서는 아직 없습니다.</p>}<div className="history-actions"><DeleteButton run={history} onStart={del.start}/></div>{armedFlowFor(del.flow,history.run_id)&&<DeleteConfirm flow={del.flow} onConfirm={()=>void del.confirm()} onCancel={del.cancel}/>}<h3>실제 사건</h3>{eventList(snapshot?.events.filter(e=>e.run_id===history.run_id)??[])}</>:<p>해당 실행 기록을 찾지 못했습니다.</p>}</>}
   {ui.popup.kind==='사건 기록'&&<>{eventList(snapshot?.events??[])}{snapshot?.events_has_more&&<p>이 창은 최근 사건만 표시합니다. 더 오래된 사건은 서버 데이터베이스에 보존되어 있습니다.</p>}</>}
   {ui.popup.kind==='제어 도구'&&<>{actual?<><p>실제 모델 응답은 서버가 처리합니다. 수동 완료·실패 대역 해제 조작은 제공하지 않습니다.</p><button className="danger" disabled={replaying||busy||current?.status!=='running'} onClick={()=>{if(current?.status==='running')void operate(()=>api.cancel(current.run_id,current.control_revision));}}>현재 실행 취소</button></>:<><p>모의 응답의 명시적인 대기 ID를 선택해 처리합니다. 실제 모델은 호출하지 않습니다.</p><p>현재 대기 {activeGates.length}건 · 이전 응답 {staleGates.length}건</p><div className="button-group"><button disabled={replaying||busy||!activeGates.length} onClick={()=>release('one','success')}>현재 1건 완료</button><button disabled={replaying||busy||!activeGates.length} onClick={()=>release('all','success')}>현재 모두 완료</button><button disabled={replaying||busy||!staleGates.length} onClick={()=>release('stale','success')}>이전 응답 돌려주기</button><button disabled={replaying||busy||!activeGates.length} onClick={()=>release('one','failure')}>실패 응답</button><button disabled={replaying||busy||!activeGates.length} onClick={()=>release('all','quality_fail')}>불합격 초안</button><button className="danger" disabled={replaying||busy||current?.status!=='running'} onClick={()=>{if(current?.status==='running')void operate(()=>api.cancel(current.run_id,current.control_revision));}}>현재 실행 취소</button></div>{!activeGates.length&&<p>현재 실행 버전에 응답 대기 중인 작업이 없어 완료·실패 조작을 할 수 없습니다.</p>}{!staleGates.length&&<p>돌려줄 이전 응답이 없습니다.</p>}{current?.status!=='running'&&<p>진행 중인 실행이 없어 취소할 수 없습니다.</p>}</>}</>}
   {ui.popup.kind==='안내'&&<><p>마을이 작업 화면입니다. 인물·말풍선은 고정된 사건의 상세를, 제출된 종이는 해당 최종 보고서를 엽니다.</p><p>주소 끝이 #/assets이면 합성 장면(에셋 실험), #/control이면 로컬 API와 보존된 기록에 연결된 실행 제어입니다. {requestLabel}. 연결이나 요청 시도는 생성·품질 검사 성공을 보장하지 않습니다.</p><p>/웹 은 웹 검색, /디비 · /DB · /데이터셋 은 내려받은 데이터셋을 선택합니다. 명령만 보내면 선택만 저장하며 연구를 시작하지 않습니다. 명령 뒤에 질문을 넣으면 그 자료로 연구합니다. /큐 와 자료 명령은 앞뒤로 함께 쓸 수 있으며 질문이 필요합니다. 진행 중 다른 자료는 /큐 로 예약하거나 다음 연구에서 선택하세요.</p><p>일반 지시는 즉시 개입하며, /큐 지시는 대기 큐에 접수됩니다. 접수와 적용은 다릅니다. 실패·취소·품질 불합격 후 큐는 직접 계속해야 합니다.</p><p>창을 닫거나 Escape를 눌러도 입력과 실행은 유지됩니다.</p><p>{connection}</p></>}
  </div></section>}
  <div ref={composerRef} className="composer-area">
   {replaying&&<div className="timeline-replay-banner"><span>읽기 전용 재생 · 입력 보존 · 실행 변경 없음</span><button onClick={()=>{playback.live();void refresh().catch(()=>{});}}>실시간 복귀</button></div>}
   {(showNotice||commandError||journalError)&&<div className="composer-notice" role={commandError||journalError?'alert':'status'}>{commandError||journalError||notice}{commandError&&<button disabled={replaying||busy} onClick={()=>void submit()}>전송 재시도</button>}<button className="notice-dismiss" aria-label="알림 닫기" onClick={()=>{setNotice('');setCommandError('');}}><Icon close/></button></div>}
   <form className="composer" onSubmit={e=>{e.preventDefault();void submit();}}>
    <textarea data-testid="command-input" aria-label="연구 지시" placeholder={ui.mode==='control'?'연구 지시를 입력하세요. /큐 로 대기열에 추가':'연구 지시 · 주소 끝을 #/control로'} value={ui.draft} onChange={e=>dispatch({type:'draft',text:e.target.value})} rows={2}/>
    <button data-testid="command-submit" className="primary" type="submit" disabled={replaying||busy||!ui.draft.trim()}>{replaying?'기록 재생 중':busy?'확인 중':'지시 보내기'}</button>
   </form>
  </div>
 <Timeline height={drawerSize.height} minHeight={drawerSize.min} maxHeight={drawerSize.max} onHeightChange={drawerSize.setPreferred} open={playback.open} runs={snapshot?.runs??[]} runId={playback.runId} archive={playback.archive} at={playback.at} replay={replaying} playing={playback.playing} loading={playback.loading} error={playback.error} onToggle={()=>playback.setOpen(!playback.open)} onSeek={playback.seek} onPlay={playback.play} onStage={playback.stage} onLive={()=>{playback.live();void refresh().catch(()=>{});}} onRetry={playback.retry} onEvent={id=>void showEvent(id)}/>
 </main>;
}
