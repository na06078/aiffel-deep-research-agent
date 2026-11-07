import type {Admission,Snapshot,SourceMode} from './계약';
export interface SourceSelection {source_mode:SourceMode;seq:number}
export function commandSource(text:string,selected?:SourceMode):SourceMode|undefined{
 let source:SourceMode|undefined;
 for(const token of text.trim().split(/\s+/)){
  if(token==='/큐')continue;
  const next=token==='/웹'?'web':['/디비','/DB','/데이터셋'].includes(token)?'local':undefined;
  if(next===undefined)break;
  if(source!==undefined&&source!==next)return undefined; // Let the server reject conflicting explicit prefixes.
  source=next;
 }
 return source??selected;
}
export function confirmedSource(previous:SourceSelection|null,value:Snapshot|Admission):SourceSelection|null{
 const snapshot='high_watermark_seq' in value;
 if(!snapshot&&value.mode==='queued')return previous;
 const seq=snapshot?value.high_watermark_seq:value.state_seq;
 if(seq===undefined||!Number.isFinite(seq)||(previous&&seq<=previous.seq))return previous;
 return {source_mode:(snapshot?value.selected_source_mode:value.source_mode)??'local',seq};
}
export function sourceLabel(mode?:SourceMode):string{return mode==='web'?'웹 검색':mode==='local'?'데이터셋':'자료 미확인';}
