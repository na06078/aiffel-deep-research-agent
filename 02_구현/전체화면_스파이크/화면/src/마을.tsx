import {useEffect,useRef,useState} from 'react';
import {Application,Assets,Container,Graphics,Rectangle,Sprite,Texture} from 'pixi.js';
import {actors as actorIds,names,villageLayout,type Actor,type Point} from './마을배치';
import {projectScene,type SceneFrame} from './장면투영';
import {drawVillage} from './마을풍경';
import type {VillageProps} from './장면계약';
import './마을.css';
type Layout=ReturnType<typeof villageLayout>;
type Frame=SceneFrame&{layout:Layout};
const characterFiles:Record<Actor,string>={planner:'조정자','researcher-1':'조사관_1','researcher-2':'조사관_2','researcher-3':'조사관_3','researcher-4':'조사관_4',editor:'종합자'};
const statusNames:Record<string,string>={idle:'대기',waiting:'응답 대기',running:'작업 중',working:'작업 중',completed:'완료',failed:'실패',cancelled:'취소',blocked:'품질 확인',interrupted:'중단'};
const toPixels=(p:Point,scale:number)=>({left:Math.round(p.x*scale),top:Math.round(p.y*scale)});
export function Village(props:VillageProps){
 const host=useRef<HTMLDivElement>(null),latest=useRef(props);latest.current=props;
 const demoStart=useRef(performance.now());
 const [ready,setReady]=useState(false),[error,setError]=useState('');
 const initial=villageLayout(window.innerWidth,window.innerHeight);
 const [frame,setFrame]=useState<Frame>(()=>({...projectScene(initial,props,0),layout:initial}));
 // props/팝업/모드 전환으로 Application을 다시 만들지 않는다.
 useEffect(()=>{demoStart.current=performance.now();},[props.demo.nonce,props.demo.action,props.mode]);
 useEffect(()=>{
  const element=host.current!,app=new Application();
  let disposed=false,initialized=false,resizeFrame=0,lastView=-Infinity;
  let layout=villageLayout(Math.max(1,element.clientWidth),Math.max(1,element.clientHeight));
  let world:Container|undefined,people={} as Record<Actor,Sprite>,paper:Graphics,cursor:Graphics,ambient:Graphics;
  let sheet:Texture,characterTextures:Record<Actor,Texture>;
  let reportId:string|undefined,reportStart=0;
  const roleStarts=new Map<Actor,{identity:string;time:number}>(),tiles=new Map<string,Texture>();
  const reducedQuery=window.matchMedia('(prefers-reduced-motion: reduce)');
  const observer=new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{if(!disposed&&world)redraw();});});
  function tile(x:number,y:number){const key=`${x},${y}`;if(!tiles.has(key))tiles.set(key,new Texture({source:sheet.source,frame:new Rectangle(x*16,y*16,16,16)}));return tiles.get(key)!;}
  function update(force=false){
   if(!world||disposed)return;
   const state=latest.current,now=performance.now();
   const elapsed=now-demoStart.current,roleElapsed:Partial<Record<Actor,number>>={};
   // 이전 호출자의 시계 없는 live 동작만 관측 시작 시각을 보존한다.
   if(!state.clock){
    for(const actor of actorIds){const view=state.actors[actor],identity=`${view.status}:${view.eventId||''}`;
     if(roleStarts.get(actor)?.identity!==identity)roleStarts.set(actor,{identity,time:now});
     roleElapsed[actor]=now-roleStarts.get(actor)!.time;
    }
    if(state.latestReport&&reportId!==state.latestReport.id){reportId=state.latestReport.id;reportStart=Date.parse(state.latestReport.createdAt)>=performance.timeOrigin?now:now-2300;}
   }
   const projection=projectScene(layout,state,elapsed,roleElapsed,state.latestReport?now-reportStart:undefined,reducedQuery.matches);
   for(const actor of actorIds){const p=projection.points[actor];people[actor].position.set(p.x,p.y);people[actor].zIndex=p.y+1;}
   paper.position.set(projection.paper.x,projection.paper.y);paper.visible=projection.paperVisible;paper.zIndex=projection.paper.y+2;
   // 코드 사건은 실제 전달된 actor의 활동 장소에 연결한다. 장식은 실행 진척이 아니다.
   const codeActor=state.code?.actor||'researcher-1',work=layout.routes[codeActor].work;
   cursor.position.set(work.x-2,work.y-8);cursor.zIndex=work.y+2;
   cursor.visible=(state.mode==='assets'?projection.phase==='working':Boolean(state.code))&&(reducedQuery.matches||Math.floor(projection.time/350)%2===0);
   ambient.clear();
   const t=reducedQuery.matches?0:projection.time;
   // 작은 수면 반짝임과 새 두 마리: recorded clock에도 순수하게 종속되는 배경 연출.
   const waterX=layout.width*.91,waterY=layout.height*.27;
   ambient.rect(Math.round(waterX-4),Math.round(waterY),4+Math.floor((Math.sin(t/900)+1)*2),1).fill(0xc1d7bf);
   for(let i=0;i<2;i++){const x=Math.round(layout.width*.24+i*10+Math.sin(t/4800+i)*5),y=Math.round(layout.height*.13+i*4);ambient.moveTo(x-2,y).lineTo(x,y+(Math.floor(t/500)%2)).lineTo(x+2,y).stroke({color:0x617053,width:1});}
   if(force||now-lastView>65){lastView=now;setFrame({...projection,layout});}
  }
  function redraw(){
   const width=element.clientWidth,height=element.clientHeight;if(!width||!height)return;
   layout=villageLayout(width,height);app.renderer.resize(width,height);
   if(world){app.stage.removeChild(world);world.destroy({children:true});}
   world=new Container();world.sortableChildren=true;world.scale.set(layout.scale);app.stage.addChild(world);
   drawVillage(world,layout,tile);
   for(const actor of actorIds){const person=new Sprite(characterTextures[actor]);person.anchor.set(.5,1);world.addChild(person);people[actor]=person;}
   paper=new Graphics().rect(-5,-12,10,13).fill(0x765740).rect(-4,-11,8,11).fill(0xfff7dc).rect(-2,-8,4,1).fill(0x9b8a68).rect(-2,-5,4,1).fill(0x9b8a68).rect(-2,-2,3,1).fill(0x9b8a68);world.addChild(paper);
   cursor=new Graphics().rect(0,0,3,1).fill(0xf8e7b4);world.addChild(cursor);
   ambient=new Graphics();ambient.zIndex=layout.height+100;world.addChild(ambient);
   update(true);app.render();
   // ready는 실제 renderer resize·좌표 적용·첫 render 이후에만 공개한다.
   setReady(true);
  }
  async function start(){
   await app.init({width:Math.max(1,element.clientWidth),height:Math.max(1,element.clientHeight),antialias:false,autoDensity:true,resolution:Math.min(window.devicePixelRatio||1,2),backgroundColor:0x94b84a,roundPixels:true});
   initialized=true;if(disposed){app.destroy(true,{children:true});return;}
   const loaded=await Promise.all([Assets.load<Texture>('/에셋/마을_타일.png'),...actorIds.map(a=>Assets.load<Texture>(`/에셋/${characterFiles[a]}.png`))]);
   if(disposed)return;
   sheet=loaded[0];characterTextures=Object.fromEntries(actorIds.map((a,i)=>[a,loaded[i+1]])) as Record<Actor,Texture>;
   loaded.forEach(texture=>{texture.source.scaleMode='nearest';});
   element.appendChild(app.canvas);redraw();observer.observe(element);app.ticker.add(()=>update());
  }
  void start().catch(cause=>{if(!disposed)setError(`마을 이미지를 불러오지 못했습니다. 새로고침해 주세요. ${String(cause)}`);});
  return ()=>{disposed=true;observer.disconnect();cancelAnimationFrame(resizeFrame);if(initialized&&app.renderer)app.destroy(true,{children:true});
   // Assets 캐시의 source는 소유하지 않는다. 잘라 만든 Texture wrapper만 해제한다.
   tiles.forEach(texture=>texture.destroy(false));tiles.clear();
  };
 },[]);
  const scale = frame.layout.scale;
  const bubblePosition=(point:Point)=>({left:Math.max(85,Math.min(frame.layout.width*scale-85,point.x*scale)),top:Math.max(90,point.y*scale-16*scale-10)});
  const demoActive = props.mode === 'assets' && props.demo.action !== 'reset';
  const plannerBubble = props.mode === 'assets' ? demoActive : Boolean(props.actors.planner.eventId);
  const codeActor = props.code?.actor || 'researcher-1';
  const codePoint = frame.layout.routes[codeActor].work;
  const viewportWidth = frame.layout.width * scale;
  const codeWidth = viewportWidth < 600 ? 135 : 155;
  const codeCenter = viewportWidth < 600
    ? (codePoint.x * scale < viewportWidth / 2 ? viewportWidth - codeWidth / 2 - 8 : codeWidth / 2 + 8)
    : Math.min(viewportWidth - codeWidth / 2 - 8, (codePoint.x + 24) * scale + 120);
  const codeVisible = props.mode === 'assets' ? ['working', 'worked', 'delivering', 'submitted'].includes(frame.phase) : Boolean(props.code);
  return <div className="village-world" data-testid="world" data-render-ready={String(ready)} data-phase={frame.phase}
    data-scene-time={Math.round(frame.time)} data-replay={String(props.clock?.replay ?? false)}
    data-layout-width={frame.layout.width * scale} data-layout-height={frame.layout.height * scale}>
    <div className="village-canvas" ref={host} aria-hidden="true" />
    {error && <p className="village-error" role="alert">{error}</p>}
    {ready && actorIds.map(actor => <span key={`place-${actor}`} className="village-place" data-place={actor}
      style={toPixels({x:frame.layout.sites[actor].x,y:frame.layout.sites[actor].y-frame.layout.routes[actor].tileSize*4-3},scale)}>{frame.layout.routes[actor].title}</span>)}
    {ready && actorIds.map(actor => <button key={actor} type="button" className="village-actor"
      data-testid={`npc-${actor}`} data-x={frame.points[actor].x} data-y={frame.points[actor].y} data-status={props.mode === 'control' ? props.actors[actor].status : 'art-demo'}
      aria-label={`${names[actor]} · ${props.mode === 'control' ? statusNames[props.actors[actor].status] || props.actors[actor].status : '장면 실험'}`}
      style={{ ...toPixels(frame.points[actor], scale), width: 18 * scale, height: 18 * scale }} onClick={() => props.onActorClick(actor)}>
      <span className="village-name">{names[actor]}{props.mode === 'control' && props.actors[actor].status === 'waiting' && <i className="village-wait" aria-label="응답 대기" />}</span>
    </button>)}
    {ready && plannerBubble && <button type="button" className="village-bubble village-planner-bubble" data-testid="bubble-planner"
      style={bubblePosition(frame.points.planner)} onClick={() => props.onActorClick('planner')}>
      <span>{props.mode === 'assets' ? '각자 맡은 일을 시작해 주세요.' : props.actors.planner.summary || '현재 지시와 배정을 확인하세요.'}</span>
      <small>(클릭해서 더 자세히)</small>
    </button>}
    {ready && props.mode === 'control' && actorIds.filter(actor => actor !== 'planner' && props.actors[actor].eventId).map(actor =>
      <button key={`bubble-${actor}`} type="button" className="village-bubble village-secondary-bubble" data-testid={`bubble-${actor}`}
        style={bubblePosition(frame.points[actor])} onClick={() => props.onActorClick(actor)}>
        <span>{props.actors[actor].summary || `${names[actor]}의 보존된 작업 사건`}</span>
        <small>(클릭해서 더 자세히)</small>
      </button>)}
    {ready && codeVisible && <button type="button" className="village-bubble village-code-bubble" data-testid="bubble-workstation"
      style={{ left: codeCenter, top: (codePoint.y + 2) * scale }} onClick={props.onCodeClick}>
      <span>...</span><small>(클릭해서 더 자세히)</small>
    </button>}
    {ready && frame.paperVisible && <button type="button" className="village-paper" data-testid="paper-document"
      data-timing-source="authored" title="문서 운반 2200ms는 장면 연출이며 실제 작업 소요시간이 아닙니다."
      style={toPixels(frame.paper, scale)} aria-label="제출 문서 열기" onClick={props.onPaperClick}>
      <span>{frame.phase === 'delivering' ? '운반 연출 · 문서 보기' : '제출 문서 · 보기'}</span>
    </button>}
  </div>;
}
