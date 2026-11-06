import {createRoot} from 'react-dom/client';
import {useEffect,useState} from 'react';
import {Village} from './마을';
import {actors} from './마을배치';
import type {VillageProps} from './장면계약';
import type {SceneAction} from './장면상태';
const presentations=Object.fromEntries(actors.map(actor=>[actor,{status:'idle'}])) as VillageProps['actors'];
function SceneCheck(){
 const [demo,setDemo]=useState<VillageProps['demo']>({action:'reset',nonce:0});
 useEffect(()=>{const change=(event:Event)=>{
  const action=(event as CustomEvent<SceneAction>).detail;
  if(['move','work','deliver','reset'].includes(action))setDemo(old=>({action,nonce:old.nonce+1}));
 };window.addEventListener('scene-check-action',change);return()=>window.removeEventListener('scene-check-action',change);},[]);
 return <Village mode="assets" demo={demo} actors={presentations} onActorClick={()=>{}} onCodeClick={()=>{}} onPaperClick={()=>{}}/>;
}
createRoot(document.getElementById('scene-check')!).render(<SceneCheck/>);
