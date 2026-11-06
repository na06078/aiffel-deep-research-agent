import {createRoot} from 'react-dom/client';
import {useEffect,useState} from 'react';
import {Village} from './마을';
import {actors} from './마을배치';
import type {VillageProps} from './장면계약';
import type {SceneAction} from './장면상태';
const presentations=Object.fromEntries(actors.map(actor=>[actor,{status:'idle'}])) as VillageProps['actors'];
function SceneCheck(){
 const [demo,setDemo]=useState<VillageProps['demo']>({action:'reset',nonce:0});
 const [view,setView]=useState<Partial<Pick<VillageProps,'mode'|'actors'|'clock'|'code'|'latestReport'>>>({});
 useEffect(()=>{const change=(event:Event)=>{
  const action=(event as CustomEvent<SceneAction>).detail;
  if(['move','work','deliver','reset'].includes(action)){setView({});setDemo(old=>({action,nonce:old.nonce+1}));}
 };const seek=(event:Event)=>setView((event as CustomEvent<typeof view>).detail);
 window.addEventListener('scene-check-action',change);window.addEventListener('scene-check-state',seek);
 return()=>{window.removeEventListener('scene-check-action',change);window.removeEventListener('scene-check-state',seek);};},[]);
 const clicked=(kind:string,actor?:string)=>window.dispatchEvent(new CustomEvent('scene-check-click',{detail:{kind,actor}}));
 return <Village mode={view.mode??'assets'} demo={demo} actors={view.actors??presentations} clock={view.clock} code={view.code} latestReport={view.latestReport}
  onActorClick={actor=>{clicked('actor',actor);}} onCodeClick={()=>{clicked('code');}} onPaperClick={()=>{clicked('paper');}}/>;
}
createRoot(document.getElementById('scene-check')!).render(<SceneCheck/>);
