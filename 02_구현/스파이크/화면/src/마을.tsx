import {useEffect,useRef,useState} from 'react';
import {Application,Assets,Container,Graphics,Rectangle,Sprite,Texture} from 'pixi.js';
import type {Phase} from './장면상태';
interface Props{phase:Phase; motionStarted:number; children?:React.ReactNode; onPaper:()=>void; paperVisible:boolean}
export function Village({phase,motionStarted,children,onPaper,paperVisible}:Props){
 const host=useRef<HTMLDivElement>(null);
 const live=useRef({phase,motionStarted,paperVisible,onPaper});live.current={phase,motionStarted,paperVisible,onPaper};
 const [ready,setReady]=useState(false),[error,setError]=useState('');
 useEffect(()=>{
  let disposed=false;let initialized=false;let destroyed=false;const app=new Application();
  function destroy(){if(initialized&&!destroyed){destroyed=true;app.destroy(true,{children:true});}}
  async function setup(){
   await app.init({width:768,height:480,background:0x8fba65,antialias:false,resolution:1,preference:'webgl',roundPixels:true});initialized=true;
   if(disposed){destroy();return;}
   const names=['마을_타일','조정자','조사관_1','조사관_2','조사관_3','조사관_4','종합자'];
   const textures=await Promise.all(names.map(name=>Assets.load<Texture>(`/에셋/${name}.png`)));
   if(disposed){destroy();return;}
   textures.forEach(texture=>{texture.source.scaleMode='nearest';});
   const world=new Container();world.scale.set(3);app.stage.addChild(world);
   const atlas=textures[0];
   function tile(col:number,row:number,x:number,y:number){const sprite=new Sprite(new Texture({source:atlas.source,frame:new Rectangle(col*16,row*16,16,16)}));sprite.position.set(x*16,y*16);world.addChild(sprite);return sprite;}
   for(let y=0;y<10;y++)for(let x=0;x<16;x++)tile((x*7+y*3)%17===0?2:(x+y)%2,0,x,y);
   for(let x=1;x<15;x++){tile(1,2,x,6);tile(1,2,x,7);}
   for(let y=3;y<9;y++)tile(1,2,8,y);
   // 같은 건축군의 지붕·벽을 맞춰 작은 집 두 채를 구성한다.
   function house(col:number,x:number,y:number){for(let r=0;r<2;r++)for(let c=0;c<3;c++)tile(col+c,4+r,x+c,y+r);tile(col+2,6,x+1,y+2);}
   house(0,2,2);house(4,11,2);
   for(const [x,y] of [[0,0],[14,0],[0,3],[14,8],[1,8],[6,0],[10,0]]){tile(4,0,x,y);tile(4,1,x,y+1);}
   for(let x=3;x<7;x++)tile(9,4,x,9);
   const workstation=new Graphics().rect(119,63,30,3).fill(0x765b43).rect(121,66,3,13).fill(0x765b43).rect(144,66,3,13).fill(0x765b43).rect(125,48,18,14).fill(0x324b46).rect(127,50,14,9).fill(0xb8d9b2).rect(132,61,4,2).fill(0x324b46).rect(149,58,11,10).fill(0xd7dfcf).rect(151,55,7,6).fill(0xfff9e8).rect(152,62,5,1).fill(0x526458);
   world.addChild(workstation);
   const positions=[[76,111],[48,133],[78,137],[183,132],[211,134],[165,93]];
   const actors=textures.slice(1).map((texture,i)=>{
    const shadow=new Graphics().ellipse(0,0,6,2).fill({color:0x334c32,alpha:.24});shadow.position.set(...positions[i] as [number,number]);world.addChild(shadow);
    const sprite=new Sprite(texture);sprite.anchor.set(.5,1);sprite.position.set(...positions[i] as [number,number]);world.addChild(sprite);return {sprite,shadow};
   });
   const paper=new Graphics().rect(0,0,9,12).fill(0xfffbeb).rect(2,3,5,1).fill(0x78917b).rect(2,6,5,1).fill(0x78917b).rect(2,9,3,1).fill(0x78917b);
   paper.eventMode='static';paper.cursor='pointer';paper.on('pointertap',()=>live.current.onPaper());world.addChild(paper);
   app.ticker.add(()=>{
    const {phase:p,motionStarted:started,paperVisible:visible}=live.current;
    const elapsed=Math.max(0,performance.now()-started);const moving=p==='moving',delivering=p==='delivering';
    const progress=Math.min(1,elapsed/(moving?1500:2200));
    const actor=actors[0];
    let x=76,y=111;
    if(moving){x=76+(135-76)*progress;y=111+(87-111)*progress;}
    else if(['at-workstation','working','worked'].includes(p)){x=135;y=87;}
    else if(delivering){x=135+(205-135)*progress;y=87+(118-87)*progress;}
    else if(p==='submitted'){x=205;y=118;}
    actor.sprite.position.set(Math.round(x),Math.round(y-(moving||delivering?Math.abs(Math.sin(elapsed/120))*1.5:0)));
    actor.shadow.position.set(Math.round(x),Math.round(y));
    paper.visible=visible||p==='worked'||delivering||p==='submitted';
    const dropped=p==='submitted'||(visible&&p!=='worked'&&p!=='delivering');
    paper.position.set(delivering?Math.round(x+7):dropped?211:148,delivering?Math.round(y-14):dropped?117:68);
    workstation.alpha=p==='working'?.82+Math.sin(elapsed/100)*.18:1;
   });
   app.canvas.setAttribute('aria-label','무료 픽셀 에셋으로 구성한 작은 조사 마을');
   host.current?.appendChild(app.canvas);app.render();setReady(true);
  }
  setup().catch(e=>{if(!disposed)setError(`마을 렌더링 실패: ${String(e)}`);});
  return()=>{disposed=true;destroy();};
 },[]);
 return <div className="village" data-testid="village" data-phase={phase} data-render-ready={ready?'true':'false'}>
  <div ref={host} className="canvas-host"/>
  {!ready&&<p className="scene-loading" role="status">{error||'마을 에셋을 불러오는 중'}</p>}
  {children}
 </div>;
}
