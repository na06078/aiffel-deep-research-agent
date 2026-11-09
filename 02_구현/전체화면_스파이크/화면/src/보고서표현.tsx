import {useEffect,useMemo,useSyncExternalStore} from 'react';
import {REPORT_RENDERER_VERSION} from './계약';
import type {ReportPresentation} from './계약';
import {api} from './통신';
import './보고서표현.css';
function safeSourceURL(value:unknown):value is string{
 if(typeof value!=='string'||!/^https?:\/\//i.test(value)||/[\u0000-\u0020\u007f]/.test(value))return false;
 try{const url=new URL(value);return (url.protocol==='http:'||url.protocol==='https:')&&!!url.hostname&&!url.username&&!url.password;}catch{return false;}
}
export function PresentationContent({state,onRetry}:{state:PresentationState;onRetry:()=>void}){
 const presentation=state.presentation;
 const links=presentation?.links.filter(link=>safeSourceURL(link.url))??[];
 return <section className="report-presentation" aria-label="보고서 본문" data-testid="report-presentation">
  <h3>보고서 본문</h3>
  {state.loading&&<p role="status">보고서 본문을 불러오는 중입니다.</p>}
  {state.error&&<div role="alert"><p>본문 조회 실패: {state.error}</p><p>아래 Markdown 원문과 다운로드는 계속 이용할 수 있습니다.</p><button type="button" disabled={state.loading} onClick={onRetry}>본문 다시 조회</button></div>}
  {presentation&&!state.loading&&!state.error&&<>
   {!!presentation.warnings.length&&<aside className="notice"><h4>표현 시 주의</h4><ul>{presentation.warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul></aside>}
   <p className="report-reading-help">본문 안에서 스크롤하여 표와 도식을 읽을 수 있습니다. 출처는 아래 출처 링크에서 열어 주세요.</p>
   <iframe className="report-presentation-frame" title="보고서 본문 · 표와 도식" sandbox="" srcDoc={presentation.html} referrerPolicy="no-referrer"/>
   <details className="report-source-links"><summary>출처 링크 ({links.length})</summary>{links.length?<ul>{links.map((link,i)=><li key={i}><a href={link.url} target="_blank" rel="noopener noreferrer">{link.text||link.url}</a></li>)}</ul>:<p>표시할 외부 출처 링크가 없습니다.</p>}</details>
  </>}
 </section>;
}
export function ReportPresentationView({reportId,sourceHash}:{reportId:string;sourceHash:string}){
 // 선택 ID+해시가 같을 때만 세션을 유지합니다. close/replay/delete의 unmount는 이전 응답을 버립니다.
 const session=useMemo(()=>createPresentationSession(reportId,sourceHash),[reportId,sourceHash]);
 const state=useSyncExternalStore(session.subscribe,session.getState,session.getState);
 useEffect(()=>{session.start();return()=>session.stop();},[session]);
 return <PresentationContent state={state} onRetry={()=>void session.refresh()}/>;
}
export type PresentationClient=Pick<typeof api,'presentation'>;
export interface PresentationState {loading:boolean;error:string;presentation:ReportPresentation|null}
export function createPresentationSession(reportId:string,sourceHash:string,client:PresentationClient=api){
 let state:PresentationState={loading:true,error:'',presentation:null};
 const listeners=new Set<()=>void>();
 const update=(next:PresentationState)=>{state=next;listeners.forEach(fn=>fn());};
 let active=false,epoch=0,reading:number|null=null;
 const current=(token:number)=>active&&epoch===token;
 async function refresh(){
  if(!active||reading===epoch)return;
  const token=epoch;reading=token;
  update({loading:true,error:'',presentation:null});
  try{
   const presentation=await client.presentation(reportId,sourceHash);
   if(!current(token))return;
   if(presentation.report_id!==reportId)throw new Error('report_id_mismatch: 선택한 보고서와 표현 응답이 다릅니다.');
   if(presentation.source_report_hash!==sourceHash)throw new Error('report_hash_mismatch: 보고서 정본이 다릅니다.');
   if(presentation.renderer_version!==REPORT_RENDERER_VERSION)throw new Error('renderer_version_mismatch: 표현 버전이 다릅니다.');
   update({loading:false,error:'',presentation});
  }catch(error){if(current(token))update({loading:false,presentation:null,error:error instanceof Error?error.message:'보고서 표현을 조회하지 못했습니다.'});}
  finally{if(reading===token)reading=null;}
 }
 return {
  getState:()=>state,
  subscribe:(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};},
  start:()=>{if(active)return;active=true;epoch++;void refresh();},
  // 관측만 종료합니다. 서버 처리·연구·변환을 시작하거나 취소하지 않습니다.
  stop:()=>{active=false;epoch++;},
  refresh,
 };
}
