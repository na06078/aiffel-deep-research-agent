import {actors,type Actor,type Point,type VillageLayout} from './마을배치';
import type {VillageProps} from './장면계약';
import {scenePhase,type Phase} from './장면상태';
export interface SceneFrame {points:Record<Actor,Point>;paper:Point;paperVisible:boolean;phase:Phase;time:number}
export const busyStatus=(status:string)=>['waiting','running','working'].includes(status);
/** 길의 길이를 기준으로 이동한다. 프레임 누적/난수/벽시계를 사용하지 않는다. */
export function routePoint(route:Point[],progress:number):Point{
 const t=Math.max(0,Math.min(1,progress));
 const lengths=route.slice(1).map((p,i)=>Math.hypot(p.x-route[i].x,p.y-route[i].y));
 let distance=lengths.reduce((a,b)=>a+b,0)*t;
 for(let i=0;i<lengths.length;i++){
  if(distance<=lengths[i]&&lengths[i]>0){const a=route[i],b=route[i+1],f=distance/lengths[i];return {x:a.x+(b.x-a.x)*f,y:a.y+(b.y-a.y)*f};}
  distance-=lengths[i];
 }
 return {...route[route.length-1]};
}
/** clock 부재 경로는 기존 호출자의 관측 경과를 받는다. */
export function projectScene(layout:VillageLayout,state:VillageProps,elapsed:number,roleElapsed:Partial<Record<Actor,number>>={},reportElapsed?:number,reduced=false):SceneFrame{
 const time=state.clock?.at_ms??elapsed;
 const roleTime=(actor:Actor)=>state.clock?Math.max(0,time-(state.clock.actor_started_ms[actor]??time)):(roleElapsed[actor]??0);
 const phase=state.mode==='assets'?scenePhase(state.demo.action,time):actors.some(a=>busyStatus(state.actors[a].status))?'working':'idle';
 const points=Object.fromEntries(actors.map(actor=>{
  const place=layout.routes[actor];let point=place.home;
  const busy=state.mode==='assets'?actor==='researcher-1'&&phase==='working':busyStatus(state.actors[actor].status);
  if(state.mode==='assets'){
   if(actor==='researcher-1'&&state.demo.action==='move')point=routePoint(place.activity,reduced?1:time/1500);
   if(actor==='researcher-1'&&state.demo.action==='work')point=place.work;
  }else if(busy)point=routePoint(place.activity,reduced?1:roleTime(actor)/1300);
  return [actor,{x:Math.round(point.x),y:Math.round(point.y+(busy&&!reduced?Math.sin(time/180)*.65:0))}];
 })) as Record<Actor,Point>;
 let paper=layout.paper,paperVisible=false,resultPhase:Phase=phase;
 const submitted=state.clock?.report_submitted_ms;
 const delivery=state.mode==='assets'&&state.demo.action==='deliver'?time:state.mode==='control'&&state.latestReport
  ?state.clock?(submitted!==undefined&&time>=submitted?time-submitted:undefined):reportElapsed:undefined;
 if(delivery!==undefined){
  paperVisible=true;const progress=Math.max(0,delivery)/2200;
  // reduced-motion에서도 사건 시각은 동일하다. 연속 이동만 생략한다.
  points.editor=routePoint(layout.routes.editor.delivery,reduced?(progress<1?0:1):progress);
  points.editor={x:Math.round(points.editor.x),y:Math.round(points.editor.y)};
  if(progress<1){paper={x:points.editor.x+7,y:points.editor.y-4};resultPhase='delivering';}else resultPhase='submitted';
 }
 return {points,paper,paperVisible,phase:resultPhase,time};
}
