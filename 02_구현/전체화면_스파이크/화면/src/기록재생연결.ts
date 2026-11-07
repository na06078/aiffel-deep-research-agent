import {useEffect,useMemo,useRef,useState} from 'react';
import type {Snapshot} from './계약';
import type {RunArchive} from './시간계약';
import {api} from './통신';
import {projectArchive,replayEnd,adjacentStage} from './시간재생';
const storageKey='village-record-replay';
function restored():{runId:string|null;at:number}{
 try{const value=JSON.parse(sessionStorage.getItem(storageKey)??'null');if(typeof value?.runId==='string'&&Number.isFinite(value.at)&&value.at>=0)return value;}catch{/* Storage may be unavailable. Replay remains usable in memory. */}
 return {runId:null,at:0};
}
// Only GET archive lives here; cursor movement never calls the execution service.
export function useRecordedReplay(snapshot:Snapshot|null){
 const [saved]=useState(restored);
 const [selectedId,setRunId]=useState<string|null>(saved.runId),[cursor,setCursor]=useState(saved.at);
 const [open,setOpen]=useState(!!saved.runId),[playing,setPlaying]=useState(false);
 const [loaded,setLoaded]=useState<{archive:RunArchive;received:number}|null>(null);
 const [loading,setLoading]=useState(false),[error,setError]=useState(''),[retry,setRetry]=useState(0),[now,setNow]=useState(0);
 const [deletedRuns,setDeletedRuns]=useState<ReadonlySet<string>>(()=>new Set());
 const knownRuns=useRef(new Set<string>());
 // Absence is deletion evidence only after this mounted hook saw the run in a snapshot.
 // Initial null snapshots and manually selected unknown IDs still get their normal GET/error.
 const missing=!!selectedId&&!!snapshot&&knownRuns.current.has(selectedId)&&!snapshot.runs.some(run=>run.run_id===selectedId);
 const runId=selectedId&&!deletedRuns.has(selectedId)&&!missing?selectedId:null;
 useEffect(()=>{snapshot?.runs.forEach(run=>knownRuns.current.add(run.run_id));},[snapshot]);
 useEffect(()=>{if(selectedId&&!runId)live();},[selectedId,runId]);
 const candidate=runId??snapshot?.current_run_id??null;
 const wanted=candidate&&!deletedRuns.has(candidate)?candidate:null;
 const watermark=runId?null:snapshot?.high_watermark_seq;
 useEffect(()=>{
  if(!wanted){setLoaded(null);setError('');setLoading(false);return;}
  let disposed=false;setLoading(true);setError('');
  api.archive(wanted).then(archive=>{if(!disposed){const received=performance.now();setLoaded({archive,received});setNow(received);}}).catch(e=>{if(!disposed)setError(String(e));}).finally(()=>{if(!disposed)setLoading(false);});
  return()=>{disposed=true;};
 },[wanted,watermark,retry]);
 const archive=loaded?.archive.run.run_id===wanted?loaded.archive:null;
 const replay=runId!==null,end=archive?replayEnd(archive):0;
 useEffect(()=>{if(replay&&archive)setCursor(n=>Math.min(n,end));},[replay,archive,end]);
 useEffect(()=>{if(replay&&!playing)return;const timer=setInterval(()=>setNow(performance.now()),50);return()=>clearInterval(timer);},[replay,playing]);
 useEffect(()=>{
  if(!playing||!replay||!archive)return;
  const started=performance.now(),from=cursor;
  const timer=setInterval(()=>{const at=Math.min(end,from+performance.now()-started);setCursor(at);if(at>=end)setPlaying(false);},50);
  return()=>clearInterval(timer);
  // Start from the selected cursor once. Each tick must not restart the timer.
 },[playing,replay,archive,end]);
 useEffect(()=>{
  try{if(runId)sessionStorage.setItem(storageKey,JSON.stringify({runId,at:cursor}));else sessionStorage.removeItem(storageKey);}catch{/* Nonpersistent browser storage is allowed. */}
 },[runId,cursor]);
 const liveAt=archive?archive.duration_ms+Math.max(0,now-(loaded?.received??now)):0;
 const at=replay?Math.min(cursor,end):archive?.run.status==='running'?liveAt:Math.min(end,liveAt);
 const projection=useMemo(()=>replay&&archive?projectArchive(archive,at):null,[replay,archive,at]);
 function select(id:string){setPlaying(false);setRunId(id||null);setCursor(0);setOpen(true);}
 function seek(ms:number){if(!archive)return;setPlaying(false);setRunId(archive.run.run_id);setCursor(Math.max(0,Math.min(end,ms)));}
 function play(){if(!archive)return;if(playing){setPlaying(false);return;}if(!replay){setRunId(archive.run.run_id);setCursor(0);}else if(cursor>=end)setCursor(0);setPlaying(true);}
 function stage(direction:-1|1){if(archive)seek(adjacentStage(archive,at,direction));}
 function live(){setPlaying(false);setRunId(null);setCursor(0);}
 // POST success is authoritative even while the journal still names this run.
 function deleted(id:string){setDeletedRuns(ids=>new Set([...ids,id]));if(runId===id)live();}
 return {open,setOpen,runId,archive,at,replay,playing,projection,loading,error,select,seek,play,stage,live,deleted,retry:()=>setRetry(n=>n+1)};
}
