import {Container,Graphics,Sprite,type Texture} from 'pixi.js';
import {actors,type Actor,type Point,type VillageLayout} from './마을배치';
type Tile=(x:number,y:number)=>Texture;
/** 기존 CC0 타일을 조립하고 작은 생활 소품은 픽셀 도형으로 그린다. 새 이미지/게임 자산은 없다. */
export function drawVillage(world:Container,l:VillageLayout,tile:Tile){
 const g=new Graphics();world.addChild(g);
 const rect=(x:number,y:number,w:number,h:number,c:number)=>g.rect(Math.round(x),Math.round(y),w,h).fill(c);
 const sprite=(texture:Texture,x:number,y:number,size=16,parent=world)=>{const s=new Sprite(texture);s.position.set(Math.round(x),Math.round(y));s.width=size;s.height=size;parent.addChild(s);return s;};
 const tree=(x:number,y:number,autumn=false)=>{
  const group=new Container();group.zIndex=y+12;world.addChild(group);
  const size=l.narrow?11:14;
  for(let row=0;row<2;row++)for(let col=0;col<2;col++)sprite(tile((autumn?9:4)+col,row),x+col*size,y-size+row*size,size,group);
 };
 const path=(points:Point[],width:number)=>{
  const line=(color:number,w:number)=>{g.moveTo(points[0].x,points[0].y);points.slice(1).forEach(p=>g.lineTo(p.x,p.y));g.stroke({color,width:w,cap:'round',join:'round'});};
  line(0x87994e,width+3);line(0xc5b67b,width);line(0xd7c38c,Math.max(2,width-4));
 };
 const fence=(x:number,y:number,count:number,vertical=false)=>{
  for(let i=0;i<count;i++){const a=x+(vertical?0:i*6),b=y+(vertical?i*6:0);rect(a,b,2,7,0x856448);rect(a,b,2,2,0xe1c08a);if(i<count-1)rect(a+1,b+3,vertical?1:6,vertical?6:1,0xb99463);}
 };
 const flowers=(x:number,y:number,count:number,parent=g)=>{for(let i=0;i<count;i++){const a=Math.round(x+(i*7)%19),b=Math.round(y+Math.floor(i/3)*5);parent.rect(a,b,1,4).fill(0x517146).rect(a-1,b,3,2).fill([0xf4d77b,0xd99b8b,0xeae2b0][i%3]);}};
 const bench=(x:number,y:number,parent=g)=>{x=Math.round(x);y=Math.round(y);parent.rect(x,y,13,4).fill(0x8a6645).rect(x,y-3,13,2).fill(0xb68b59).rect(x+1,y+4,2,3).rect(x+10,y+4,2,3).fill(0x684e3c);};
 // 잔디는 가장자리까지 이어진다. 밀도는 숲/빈터/텃밭마다 다르게 구성한다.
 for(let y=0;y<l.height;y+=16)for(let x=0;x<l.width;x+=16){const hash=(x*17+y*13)%97;sprite(tile(hash<5?2:0,0),x,y);}
 // 땅/물은 가장 아래, 나무/건물/사람은 발밑 y 깊이 정렬이다.
 g.zIndex=1;
 const streamX=Math.round(l.width*(l.narrow?.91:.91));
 const water=new Graphics();water.zIndex=.5;world.addChild(water);
 const waterLine=(color:number,width:number)=>{water.moveTo(streamX+11,-20).bezierCurveTo(streamX-30,l.height*.24,streamX+21,l.height*.44,streamX-4,l.height*.65).bezierCurveTo(streamX-17,l.height*.8,streamX+20,l.height*.9,streamX+6,l.height+20).stroke({color,width,cap:'round'});};
 waterLine(0x647f50,l.narrow?25:36);waterLine(0x91ab69,l.narrow?21:31);waterLine(0x508e9a,l.narrow?16:24);waterLine(0x69a4ac,l.narrow?10:17);
 // 작은 연못과 이어진 개울: 뚜렷한 물가 동네, 획일적인 테두리 나무는 두지 않는다.
 const pondX=l.width*.82,pondY=l.height*.39;
 water.ellipse(pondX,pondY,l.narrow?13:30,l.narrow?12:19).fill(0x7d9958).ellipse(pondX,pondY-1,l.narrow?10:26,l.narrow?9:15).fill(0x5996a2);
 for(let i=0;i<8;i++)water.rect(streamX-4+(i%3)*3,i*l.height/8,5,1).fill(0x9cc5bc);
 path(l.street,l.narrow?8:12);
 for(const actor of actors){const p=l.routes[actor];path(p.activity,l.narrow?5:7);path(p.delivery,l.narrow?5:7);}
 const sq=l.square,short=l.height<220;
 // 두 주거 골목은 업무 목적지가 아니다. 화면 비율별로 저작한 집/마당이며 NPC를 늘리지 않는다.
 const positions=l.narrow?[[.15,.17],[.19,.24],[.14,.71],[.35,.80]]:short?[[.06,.61],[.15,.79],[.78,.80],[.84,.88]]:[[.14,.13],[.27,.15],[.30,.30],[.10,.68],[.18,.79],[.36,.88]];
 const styles=['gable','long-roof','porch','lean-to','porch','gable'];
 const homes=positions.map(([x,y],i)=>{const n=l.narrow||short?6:7,columns=i%3===1?4:3;return {id:`cottage-${i}`,pocket:i<positions.length/2?'orchard-lane':'garden-lane',x:Math.round(x*l.width),y:Math.round(y*l.height),width:columns*n,height:4*n,style:styles[i],n,columns};});
 for(const pocket of ['orchard-lane','garden-lane']){
  const group=homes.filter(h=>h.pocket===pocket).reverse(),entry=group[0],side=entry.x+entry.width/2+6;
  const junction=pocket==='orchard-lane'?l.street[l.narrow?0:1]:l.street[l.narrow?3:2];
  const approach=l.narrow&&pocket==='orchard-lane'?[junction,{x:junction.x,y:entry.y+11}]:short&&pocket==='garden-lane'?[sq,{x:sq.x-25,y:sq.y+52},{x:side,y:sq.y+52},{x:side,y:entry.y+11}]:[junction,{x:side,y:junction.y},{x:side,y:entry.y+11}];
  const lane=group.flatMap((h,i)=>i?[{x:h.x,y:group[i-1].y+11},{x:h.x,y:h.y+11}]:[{x:h.x,y:h.y+11}]);
  path([...approach,...lane],l.narrow?4:5);
 }
 for(const [i,h] of homes.entries()){
  const left=h.x-h.width/2,top=h.y-h.height;
  // 출입구를 남긴 작은 앞마당. 울타리와 화단이 집마다 이어진다.
  rect(left-3,h.y+1,h.width+6,12,0xa5b76b);fence(left-4,h.y+12,Math.max(2,Math.floor(h.width/6)-1));
  path([{x:h.x,y:h.y+1},{x:h.x,y:h.y+12}],3);
  flowers(left-1,h.y+3,3);
  rect(left+h.width-1,h.y+2,2,8,0x795c40);rect(left+h.width-3,h.y+1,6,4,i%2?0xb57053:0x7b9382); // 우편함
  const house=new Container();house.label=h.id;house.zIndex=h.y;world.addChild(house);
  const shade=new Graphics().rect(left+2,top+4,h.width+2,h.height).fill({color:0x3e5939,alpha:.2});house.addChild(shade);
  for(let row=0;row<4;row++)for(let col=0;col<h.columns;col++){
   const source=col===0?0:col===h.columns-1?2:1;
   sprite(tile((i%2?0:4)+source,4+row),left+col*h.n,top+row*h.n,h.n,house);
  }
  const detail=new Graphics();house.addChild(detail);
  const cr=(x:number,y:number,w:number,h:number,c:number)=>detail.rect(Math.round(x),Math.round(y),w,h).fill(c);
  cr(left+h.width-7,top-3,3,8,0x776557);cr(left+h.width-8,top-3,5,2,0xc4b69b);
  if(h.style==='porch'){
   cr(left+3,h.y-7,h.width-6,3,0xb58b55);cr(left+4,h.y-4,1,6,0x74533b);cr(left+h.width-5,h.y-4,1,6,0x74533b);cr(left+3,h.y+1,h.width-6,2,0xd0b47a);
  }else if(h.style==='lean-to'){
   cr(left+h.width-8,h.y-12,8,12,0xa4845b);cr(left+h.width-9,h.y-14,10,3,0x79594b);cr(left+h.width-6,h.y-9,3,5,0x5a7971);
  }else if(h.style==='gable'){
   cr(h.x-4,top+8,8,7,0x9c684f);cr(h.x-2,top+10,4,4,0xe0ce91);cr(h.x-5,top+7,10,2,0x725047);
  }else{
   cr(left+3,h.y-9,6,5,0x658a81);cr(left+2,h.y-4,8,2,0xd6b97d);
  }
  if(i%2===0){
   // 빨랫줄은 길 위가 아닌 집 옆 마당 안에 둔다.
   rect(left+3,h.y+3,1,7,0x856448);rect(left+h.width-4,h.y+3,1,7,0x856448);rect(left+3,h.y+3,h.width-6,1,0xd8c497);
   rect(left+6,h.y+4,4,5,0xf2e5be);rect(left+12,h.y+4,3,4,0x819d9b);
  }
 }
 // 길의 교차점이 아니라 장터가 둘러싼 돌바닥 광장. 운반 종착점 주변은 비워 둔다.
 const rx=l.narrow?24:short?26:42,ry=l.narrow?19:short?13:25;
 const plaza=new Graphics();plaza.label='market-square';plaza.zIndex=1;world.addChild(plaza);
 plaza.roundRect(sq.x-rx-2,sq.y-ry-2,rx*2+4,ry*2+4,5).fill(0x8c9c65).roundRect(sq.x-rx,sq.y-ry,rx*2,ry*2,4).fill(0xb6b092);
 for(let y=-ry+2;y<ry-2;y+=5)for(let x=-rx+2;x<rx-3;x+=8)plaza.rect(sq.x+x+(y%2?1:0),sq.y+y,6,3).fill((x+y)%3?0xd3c9a4:0xc7be98);
 const furnishings=new Graphics();furnishings.label='square-furnishings';furnishings.zIndex=2;world.addChild(furnishings);
 furnishings.rect(sq.x-11,sq.y-7,10,8).fill(0x777e6c).rect(sq.x-10,sq.y-6,8,3).fill(0x465e60).rect(sq.x-12,sq.y-9,12,3).fill(0xd4c4a3);
 // 집과 다른 열린 실루엣: 줄무늬 차양 아래 농산물 가판대.
 for(let i=0;i<2;i++){
  const x=sq.x+(i?rx-14:-rx+2),y=sq.y-ry+2,w=l.narrow?13:18;
  const stall=new Graphics();stall.label=`market-stall-${i}`;stall.zIndex=y+12;world.addChild(stall);
  stall.rect(x,y+5,2,10).rect(x+w-2,y+5,2,10).fill(0x77573d).rect(x,y+9,w,5).fill(0x977047);
  stall.rect(x-1,y-1,w+2,6).fill(i?0x799185:0xb57857);
  for(let a=1;a<w;a+=5)stall.rect(x+a,y-1,2,6).fill(0xe5d4a6);
  for(let a=2;a<w-2;a+=4)stall.rect(x+a,y+8,3,3).fill(i?0xb1bd68:0xd99750);
 }
 // 광장 남쪽의 벤치·꽃섬은 작은 이야기를 만든다. 가운데 통로와 문서는 가리지 않는다.
 bench(sq.x-rx+3,sq.y+ry-2,furnishings);bench(sq.x+rx-15,sq.y+ry-2,furnishings);
 flowers(sq.x-rx-5,sq.y+3,6,furnishings);flowers(sq.x+rx-3,sq.y+6,5,furnishings);
 furnishings.rect(sq.x+rx-4,sq.y+ry-6,7,4).fill(0x947347).rect(sq.x+rx-3,sq.y+ry-8,5,3).fill(0xbda570);
 // 개울을 건너는 목교: 길은 물 위에서 끊기지 않는다.
 const bridgeY=l.narrow?l.street.at(-1)!.y:l.street.at(-1)!.y;
 rect(streamX-18,bridgeY-5,38,12,0x73513b);
 for(let x=-17;x<20;x+=4)rect(streamX+x,bridgeY-4,3,10,0xc29c64);
 rect(streamX-19,bridgeY-7,39,2,0x694d3a);rect(streamX-19,bridgeY+7,39,2,0x694d3a);
 // 구성된 숲 무리와 열린 초지. 모든 빈칸을 나무로 채우지 않는다.
 const groves=l.narrow?[[.03,.035],[.13,.05],[.01,.35],[.83,.08],[.92,.16],[.07,.79],[.15,.86],[.02,.93],[.79,.94]]:[[.025,.06],[.07,.10],[.025,.20],[.065,.27],[.84,.08],[.92,.13],[.97,.24],[.025,.76],[.05,.86],[.18,.95],[.24,.97],[.89,.83],[.96,.90]];
 groves.forEach(([x,y],i)=>tree(Math.round(l.width*x),Math.round(l.height*y),i%5===2));
 flowers(l.width*.12,l.height*.58,6);flowers(l.width*.68,l.height*.86,8);flowers(sq.x-28,sq.y-19,5);
 for(const actor of actors){
  const site=l.sites[actor],p=l.routes[actor],n=p.tileSize,w=p.columns*n;
  const building=new Container();building.zIndex=site.y;world.addChild(building);
  // 반복 가능한 가운데 지붕과 끝 조각을 조립해 실제 폭이 서로 다른 건물을 만든다.
  const base=p.red?4:0,left=site.x-w/2,top=site.y-4*n;
  const shadow=new Graphics().rect(left+3,top+4,w,4*n+2).fill({color:0x3e5939,alpha:.22});building.addChild(shadow);
  for(let row=0;row<4;row++)for(let col=0;col<p.columns;col++){
   let source=col===0?0:col===p.columns-1?2:1;
   if(row===3&&col>0&&col<p.columns-1&&col!==Math.floor(p.columns/2))source=0;
   sprite(tile(base+source,4+row),left+col*n,top+row*n,n,building);
  }
  // 소품은 각 장소의 정체성을 만든다. 모든 집 옆에 같은 PC를 놓지 않는다.
  const {x,y}=p.work;
  if(actor==='planner'){
   rect(x-9,y-6,18,4,0x8c6749);rect(x-7,y-5,14,2,0xd2bb86);rect(x-8,y-2,2,6,0x654f3b);rect(x+6,y-2,2,6,0x654f3b);
   rect(x-4,y-7,7,2,0xf4eacb);fence(left-8,site.y+3,3,true);flowers(left-13,site.y-7,5);
  }else if(actor==='researcher-1'){
   rect(x-9,y-12,15,12,0x785338);for(let i=0;i<5;i++)rect(x-7+i*2,y-10,1,7,[0xbb7457,0x9ca971,0xd6c18f][i%3]);rect(x-9,y-5,15,1,0xbd965c);
   bench(left-10,site.y+11);tree(left-22,site.y-19);flowers(site.x+18,site.y-1,4);
  }else if(actor==='researcher-2'){
   const gardenX=l.narrow?site.x-18:site.x+8,gardenY=site.y+8;
   rect(gardenX,gardenY, l.narrow?30:43,17,0x8b6c43);
   for(let row=0;row<3;row++)for(let col=0;col<5;col++){const a=gardenX+3+col*(l.narrow?5:8),b=gardenY+2+row*5;rect(a,b,3,3,0x567a3d);rect(a+1,b-1,1,3,row===1?0xe4ac52:0x9bab4d);}
   fence(left-5,site.y-4,3,true);fence(gardenX,gardenY+19,l.narrow?5:7);flowers(left-6,site.y-16,4);
  }else if(actor==='researcher-3'){
   rect(x-9,y-6,17,4,0x8a6240);rect(x-7,y-2,2,5,0x5c4736);rect(x+5,y-2,2,5,0x5c4736);
   rect(x-5,y-9,8,3,0xd2b17b);rect(x+3,y-12,2,8,0x626a60);rect(x+1,y-13,7,3,0xa6aea0);
   for(let i=0;i<4;i++)rect(left-9+i*4,site.y+1,3,8,0xab8355);fence(site.x+15,site.y+9,4);
  }else if(actor==='researcher-4'){
   rect(x-7,y-8,13,8,0x684e39);rect(x-6,y-7,11,5,0x9db8aa);rect(x-1,y-11,2,4,0xc6b982);
   for(let i=0;i<5;i++){rect(x+9+i*3,y-3+(i%2)*3,1,7,0x697e42);rect(x+9+i*3,y-4+(i%2)*3,2,2,0xb29559);}
   bench(left-4,site.y+12);
  }else{
   // 작동 사건의 PC는 편집소의 인쇄 작업대에만 존재한다.
   rect(x-10,y-5,18,4,0x7c5c43);rect(x-8,y-15,12,10,0x465455);rect(x-7,y-14,10,7,0x8bbab7);rect(x-3,y-6,4,2,0x384748);
   rect(x+5,y-8,6,6,0xe9dbb7);rect(site.x+16,site.y-3,6,9,0x995c48);rect(site.x+15,site.y-7,8,6,0xba7555);rect(site.x+16,site.y-5,6,1,0x5d4f3b);
   fence(left-9,site.y+4,3,true);flowers(left-14,site.y-14,4);
  }
 }
 return {streamX,pondX,pondY,homes};
}
