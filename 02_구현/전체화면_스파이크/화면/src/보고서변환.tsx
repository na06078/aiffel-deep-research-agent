import {useEffect,useMemo,useSyncExternalStore} from 'react';
import './보고서변환.css';
import type {ExportFormat,ReportExport} from './계약';
import {api} from './통신';
export type ExportClient = Pick<typeof api,'exports'|'createExport'>;
export interface ExportViewState {
 items: Partial<Record<ExportFormat,ReportExport>>;
 pending: Partial<Record<ExportFormat,boolean>>;
 errors: Partial<Record<ExportFormat,string>>;
 loaded: boolean; loading: boolean; readError: string;
}
export function createExportSession(reportId:string,sourceHash:string,client:ExportClient=api) {
 let state:ExportViewState={items:{},pending:{},errors:{},loaded:false,loading:true,readError:''};
 const listeners=new Set<()=>void>();
 const update=(patch:Partial<ExportViewState>)=>{state={...state,...patch};listeners.forEach(fn=>fn());};
 let active=false,epoch=0;
 let readingEpoch:number|null=null;
 // POST의 시작과 종료를 모두 세어, 그 사이 시작한 GET도 완료 상태를 되돌리지 못하게 합니다.
 const revisions:Record<ExportFormat,number>={html:0,pdf:0};
 let timer:ReturnType<typeof setTimeout>|undefined;
 const current=(token:number)=>active&&token===epoch;
 function validate(value:{report_id:string;source_report_hash:string}){
  if(value.report_id!==reportId||value.source_report_hash!==sourceHash)throw new Error('report_hash_mismatch: 선택한 보고서의 정본과 변환 응답이 다릅니다.');
 }
 function schedule(){
  if(timer!==undefined)clearTimeout(timer);
  timer=undefined;
  if(active&&!state.readError&&Object.values(state.items).some(value=>value?.status==='generating'))timer=setTimeout(()=>{timer=undefined;void refresh();},750);
 }
 async function refresh(){
  if(!active||readingEpoch===epoch)return;
  const token=epoch,captured={...revisions};
  readingEpoch=token;
  update({loading:true});
  try{
   const result=await client.exports(reportId);
   if(!current(token))return;
   validate(result);result.exports.forEach(validate);
   const items={...state.items};
   for(const value of result.exports){if(captured[value.format]===revisions[value.format]&&!state.pending[value.format])items[value.format]=value;}
   update({items,loaded:true,loading:false,readError:''});
  }catch(error){if(current(token))update({items:{},loading:false,readError:error instanceof Error?error.message:'변환 상태를 확인하지 못했습니다.'});}
  finally{if(readingEpoch===token)readingEpoch=null;}
  if(current(token))schedule();
 }
 async function create(format:ExportFormat){
  if(!active||!state.loaded||state.pending[format]||!['not_requested','failed'].includes(state.items[format]?.status??''))return;
  const token=epoch;
  revisions[format]++;
  update({pending:{...state.pending,[format]:true},errors:{...state.errors,[format]:''}});
  try{
   const result=await client.createExport(reportId,format,sourceHash);
   if(!current(token))return;
   validate(result);
   if(result.format!==format)throw new Error('report_hash_mismatch: 요청한 변환 형식과 응답이 다릅니다.');
   update({items:{...state.items,[format]:result}});
  }catch(error){if(current(token))update({errors:{...state.errors,[format]:error instanceof Error?error.message:'변환 생성 요청에 실패했습니다.'}});}
  finally{if(current(token)){revisions[format]++;update({pending:{...state.pending,[format]:false}});schedule();}}
 }
 return {
  getState:()=>state,
  subscribe:(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};},
  start:()=>{if(active)return;active=true;epoch++;void refresh();},
  // 클라이언트 관측만 닫습니다. 서버 변환 취소나 연구/큐 API는 호출하지 않습니다.
  stop:()=>{active=false;epoch++;if(timer!==undefined)clearTimeout(timer);timer=undefined;},
  refresh,
  create,
 };
}
export function ExportControls({state,onRefresh,onCreate}:{state:ExportViewState;onRefresh:()=>void;onCreate:(format:ExportFormat)=>void}) {
 return <section className="report-exports" data-testid="report-exports" aria-label="보고서 형식 변환">
  <h3>HTML · PDF</h3>
  <p>같은 Markdown 정본을 변환합니다. 연구를 다시 실행하지 않습니다.</p>
  {state.loading&&!state.loaded&&<p role="status">변환 상태를 조회하는 중입니다.</p>}
  {state.readError&&<div role="alert"><p>변환 상태 조회 실패: {state.readError}</p><button className="report-export-retry" type="button" disabled={state.loading} onClick={onRefresh}>{state.loading?'다시 조회 중':'다시 조회'}</button></div>}
  {(['html','pdf'] as const).map(format=>{
   const value=state.items[format],label=format.toUpperCase();
   const pending=!!state.pending[format],generating=value?.status==='generating';
   const requestError=state.errors[format];
   const retry=!!requestError||value?.status==='failed';
   const ready=!state.readError&&value?.status==='ready'&&value.download_url&&value.sha256;
   return <div key={format} className="report-export-row">
    <div className="report-export-info"><strong>{label}</strong>
     {pending?<p role="status">{label} 생성 요청 중입니다.</p>:generating?<p role="status">{label} 생성 중입니다. 창을 닫아도 서버 변환은 계속될 수 있습니다.</p>:value?.status==='not_requested'?<small>아직 생성하지 않았습니다.</small>:ready?<small>다운로드 준비 완료</small>:null}
     {value?.status==='failed'&&!pending&&<p role="alert">{label} 변환 실패: {value.error?`${value.error.code}: ${value.error.message}`:'원인을 확인하지 못했습니다. 다시 시도해 주세요.'}</p>}
     {requestError&&<p role="alert">{label} 생성 요청 실패: {requestError}</p>}
    </div>
    {ready?<a className="download" data-testid={`export-${format}-download`} href={value.download_url!} download>{label} 다운로드</a>:<button type="button" data-testid={`export-${format}-create`} disabled={!state.loaded||!!state.readError||pending||generating||!value} onClick={()=>onCreate(format)}>{label} {retry?'다시 시도':pending?'요청 중':generating?'생성 중':'생성'}</button>}
   </div>;
  })}
 </section>;
}
export function ReportExportsView({reportId,sourceHash}:{reportId:string;sourceHash:string}) {
 // 보고서 선택 수명만 구독합니다. 전역 스냅샷 갱신은 포커스나 입력을 재설정하지 않습니다.
 const session=useMemo(()=>createExportSession(reportId,sourceHash),[reportId,sourceHash]);
 const state=useSyncExternalStore(session.subscribe,session.getState,session.getState);
 useEffect(()=>{session.start();return()=>session.stop();},[session]);
 return <ExportControls state={state} onRefresh={()=>void session.refresh()} onCreate={format=>void session.create(format)}/>;
}
