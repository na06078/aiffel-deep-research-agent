import {useCallback,useEffect,useRef,useState} from 'react';
import type {Snapshot,JournalEvent} from './계약';
import {api} from './통신';
import {acceptSnapshot} from './상태';
export function useJournal(){
 const [snapshot,setSnapshot]=useState<Snapshot|null>(null);
 const [connection,setConnection]=useState('연결 준비');
 const [error,setError]=useState('');
 const refresh=useCallback(async()=>{const next=await api.snapshot();setSnapshot(old=>acceptSnapshot(old,next));return next;},[]);
 useEffect(()=>{
  let disposed=false; let source:EventSource|null=null; let timer:ReturnType<typeof setTimeout>|undefined;
  let retry:ReturnType<typeof setTimeout>|undefined;
  async function start(){
   try{
    const initial=await refresh();if(disposed)return;
    source=new EventSource(`/api/spike/events?after=${initial.high_watermark_seq}`);
    source.onopen=()=>{setConnection('기록 실시간 연결');setError('');};
    source.onmessage=(message)=>{
     try{const event=JSON.parse(message.data) as JournalEvent;if(typeof event.seq!=='number')throw new Error('잘못된 사건 형식');
      clearTimeout(timer);timer=setTimeout(()=>{refresh().catch(e=>setError(String(e)));},60);
     }catch(e){setError(String(e));}
    };
    source.onerror=()=>setConnection('연결 재시도 중 · 서버 기록은 보존됩니다');
   }catch(e){if(disposed)return;setError(String(e));setConnection('서버 연결 실패');retry=setTimeout(start,2500);}
  }
  void start();
  return()=>{disposed=true;source?.close();clearTimeout(timer);clearTimeout(retry);};
 },[refresh]);
 return {snapshot,refresh,connection,error};
}
