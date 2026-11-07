import {expect,it} from 'vitest';
import {actors,villageLayout} from './마을배치';
import type {VillageProps} from './장면계약';
import {projectScene,routePoint} from './장면투영';
import {villageMap} from './마을지도';
const layout=villageLayout(1440,900);
const base:VillageProps={mode:'control',demo:{action:'reset',nonce:0},actors:Object.fromEntries(actors.map(a=>[a,{status:'idle'}])) as VillageProps['actors'],onActorClick:()=>{},onCodeClick:()=>{},onPaperClick:()=>{}};
it('기록 시각2200→500→2200은 벽시계/호출 순서와 무관하게 동일한 실제 좌표다',()=>{
 const state={...base,actors:{...base.actors,'researcher-1':{status:'waiting'}},clock:{at_ms:2200,actor_started_ms:{'researcher-1':0},replay:true}} as VillageProps;
 const first=projectScene(layout,state,7);
 expect(first.points['researcher-1']).toEqual(layout.routes['researcher-1'].work);
 const rewind=projectScene(layout,{...state,clock:{...state.clock!,at_ms:500}},9000);
 expect(rewind.points['researcher-1']).not.toEqual(first.points['researcher-1']);
 const again=projectScene(layout,state,40000);
 expect(again).toEqual(first);
 expect(first.time).toBe(2200);
});
it('제출 전에는 종이가 없고 실제 제출 뒤2200ms의 연출 뒤에만 내려놓는다',()=>{
 const state:VillageProps={...base,latestReport:{id:'actual-report',title:'실제 제출',createdAt:'2026-11-06T00:00:00Z'},clock:{at_ms:999,actor_started_ms:{},report_submitted_ms:1000,replay:true}};
 const at=(time:number)=>projectScene(layout,{...state,clock:{...state.clock!,at_ms:time}},80000,{},90000);
 expect(at(999).paperVisible).toBe(false);
 expect(at(1000).phase).toBe('delivering');
 expect(at(3199).phase).toBe('delivering');
 expect(at(3200).phase).toBe('submitted');
 expect(at(3200).paper).toEqual(layout.paper);
 expect(at(1500)).toEqual(at(1500));
 expect(at(500).paperVisible).toBe(false);
 expect(projectScene(layout,{...state,latestReport:undefined},0).paperVisible).toBe(false);
});
it('일시정지 시각의 바운스·운반 위치는 벽시계가 지나도 그대로다',()=>{
 const state:VillageProps={...base,actors:{...base.actors,planner:{status:'running'}},latestReport:{id:'report',title:'실제 제출',createdAt:'2026-11-06T00:00:00Z'},clock:{at_ms:1400,actor_started_ms:{planner:100},report_submitted_ms:1000,replay:true}};
 const paused=projectScene(layout,state,0,{planner:9000},60000);
 expect(paused.phase).toBe('delivering');
 expect(paused.paper).not.toEqual(layout.paper);
 expect(projectScene(layout,state,120000,{planner:120000},120000)).toEqual(paused);
});
it('동작 줄이기는 경로 이동과 바운스만 줄이며 제출의2200ms 시간 경계를 바꾸지 않는다',()=>{
 const state:VillageProps={...base,latestReport:{id:'report',title:'실제 제출',createdAt:'2026-11-06T00:00:00Z'},clock:{at_ms:1500,actor_started_ms:{},report_submitted_ms:1000,replay:true}};
 expect(projectScene(layout,state,0,{},0,true).phase).toBe('delivering');
 expect(projectScene(layout,{...state,clock:{...state.clock!,at_ms:3200}},0,{},0,true).phase).toBe('submitted');
});
it('자산 실험의 작업 연출은1600ms에 끝나고 그 뒤에는 바운스가 누적되지 않는다',()=>{
 const state:VillageProps={...base,mode:'assets',demo:{action:'work',nonce:1}};
 expect(projectScene(layout,state,1599).phase).toBe('working');
 expect(projectScene(layout,state,1600).phase).toBe('worked');
 expect(projectScene(layout,state,1700).points['researcher-1']).toEqual(projectScene(layout,state,1900).points['researcher-1']);
});
it('새 지형에서도 실제 busy와 기록된 제출의 좌표는 같은 보행 망을 따르고 종착점·재생 결정성을 보존한다',()=>{
 for(const [width,height] of [[1440,900],[1100,720],[390,844],[844,390],[640,360]]){
  const l=villageLayout(width,height),m=villageMap(l);
  const state:VillageProps={...base,actors:Object.fromEntries(actors.map(a=>[a,{status:'running'}])) as VillageProps['actors'],clock:{at_ms:0,actor_started_ms:Object.fromEntries(actors.map(a=>[a,0])),replay:true}};
  for(const time of [0,200,650,1000,1300,2200]){
   const f=projectScene(l,{...state,clock:{...state.clock!,at_ms:time}},88888);
   for(const actor of actors)expect(m.roads.contains(f.points[actor]),`${width}: ${actor} @ ${time}`).toBe(true);
   expect(projectScene(l,{...state,clock:{...state.clock!,at_ms:time}},0)).toEqual(f);
  }
  const submitted={...state,latestReport:{id:'preserved-report',title:'기록된 제출',createdAt:'2026-11-06T00:00:00Z'}};
  for(const time of [1000,1300,2100,3199,3200]){
   const f=projectScene(l,{...submitted,clock:{...state.clock!,at_ms:time,report_submitted_ms:1000}},0);
   expect(m.roads.contains(f.points.editor)).toBe(true);
   if(time===3200){expect(f.paper).toEqual(l.paper);expect(f.phase).toBe('submitted');}
  }
 }
});
it('다중 구간 경로는 길이를 기준으로 진행하며 끝에서 멈춘다',()=>{
 const road=[{x:0,y:0},{x:0,y:10},{x:30,y:10}];
 expect(routePoint(road,.5)).toEqual({x:10,y:10});
 expect(routePoint(road,-1)).toEqual({x:0,y:0});
 expect(routePoint(road,3)).toEqual({x:30,y:10});
});
