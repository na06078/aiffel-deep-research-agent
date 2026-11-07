import {Container,Graphics,Sprite,type Texture} from 'pixi.js';
import {actors,type Actor,type Point,type VillageLayout} from './마을배치';
import {villageMap,type Home} from './마을지도';
type Tile=(x:number,y:number)=>Texture;

/** 도로·수면·집·숲은 같은 순수 지도에서 읽는다. 원본 atlas는 바꾸지 않는다. */
export function drawVillage(world:Container,l:VillageLayout,tile:Tile) {
 const map=villageMap(l),compact=l.narrow||l.height<250;
 const layer=(label:string,z:number)=>{const g=new Graphics();g.label=label;g.zIndex=z;world.addChild(g);return g;};
 const rect=(g:Graphics,x:number,y:number,w:number,h:number,color:number)=>g.rect(Math.round(x),Math.round(y),w,h).fill(color);
 const sprite=(parent:Container,x:number,y:number,n:number,cx:number,cy:number)=>{
  const s=new Sprite(tile(cx,cy));s.position.set(Math.round(x),Math.round(y));s.width=n;s.height=n;parent.addChild(s);return s;
 };
 const grass=new Container();grass.zIndex=-10;world.addChild(grass);
 for(let y=0;y<l.height;y+=16)for(let x=0;x<l.width;x+=16)sprite(grass,x,y,16,0,0);
 const ground=layer('village-ground',-8),water=layer('village-water',-6);
 // 작은 계단형 둑을 먼저 이어 붙이고 수면을 한 번에 덮는다. 물에는 도로가 없다.
 for(const b of map.water)water.rect(b.x-4,b.y-2,b.width+8,b.height+4);
 water.fill(0x78915b);
 for(const b of map.water)water.rect(b.x-2,b.y,b.width+4,b.height);
 water.fill(0xa1b579);
 for(const b of map.water)water.rect(b.x,b.y,b.width,b.height);
 water.fill(0x568f9d);
 for(const b of map.water)water.rect(b.x+3,b.y,b.width-6,b.height);
 water.fill(0x6da5ae);
 for(const [i,b] of map.water.entries())if(i%9===2)rect(water,b.x+4,b.y+1,Math.min(7,b.width-5),1,0xa9c9be);
 // 마당은 문 앞을 비우고 집 옆 생활 공간만 받친다.
 for(const h of map.homes){
  rect(ground,h.x-h.width/2-3,h.y+1,h.width+6,10,0xaabd75);
  flowers(ground,h.x-h.width/2,h.y+2,3);
 }
 const road=layer('village-roads',-4);
 paintCells(road,map.roads.cells,0xcdbb83);
 paintCells(road,map.roads.edge,0x9b9c67);
 const sq=l.square,rx=l.narrow?25:compact?30:50,ry=l.narrow?19:compact?14:28;
 const plaza=layer('market-square',-3);
 // 광장에는 따로 테두리를 긋지 않는다. 흙길과 포장은 같은 보행 면이다.
 rect(plaza,sq.x-rx+1,sq.y-ry+1,rx*2-2,ry*2-2,0xc4bea0);
 for(let y=sq.y-ry+3;y<sq.y+ry-3;y+=6)for(let x=sq.x-rx+3;x<sq.x+rx-5;x+=9){
  rect(plaza,x+((y%12)?2:0),y,6,3,0xd3caab);
 }
 const bridge=layer('village-bridge',0),b=map.bridge;
 rect(bridge,b.x,b.y,b.width,b.height,0x77583f);
 for(let x=b.x+1;x<b.x+b.width-1;x+=4)rect(bridge,x,b.y+2,3,b.height-4,0xc49f69);
 rect(bridge,b.x,b.y,b.width,2,0x8d704d);rect(bridge,b.x,b.y+b.height-2,b.width,2,0x8d704d);
 for(const x of [b.x,b.x+b.width-2]){rect(bridge,x,b.y-2,2,5,0x6d513a);rect(bridge,x,b.y+b.height-3,2,5,0x6d513a);}
 const furnishings=layer('square-furnishings',2);
 bench(furnishings,sq.x-rx+2,sq.y+ry-4);bench(furnishings,sq.x+rx-15,sq.y+ry-4);
 flowers(furnishings,sq.x-rx-8,sq.y+ry-6,4);flowers(furnishings,sq.x+rx+2,sq.y+ry-4,4);
 for(let i=0;i<2;i++){
  const x=sq.x+(i?rx-16:-rx+2),y=sq.y-ry+2,w=compact?13:18;
  const stall=layer(`market-stall-${i}`,y+12);
  rect(stall,x,y+4,2,11,0x77573d);rect(stall,x+w-2,y+4,2,11,0x77573d);
  rect(stall,x,y+9,w,5,0x977047);rect(stall,x-1,y-1,w+2,6,i?0x799185:0xb57857);
  for(let a=1;a<w;a+=5)rect(stall,x+a,y-1,2,6,0xe5d4a6);
  for(let a=2;a<w-2;a+=4)rect(stall,x+a,y+8,3,3,i?0xb1bd68:0xd99750);
 }
 const house=(id:string,x:number,y:number,columns:number,n:number,red:boolean,rows=4)=>{
  const group=new Container();group.label=id;group.zIndex=y;world.addChild(group);
  const left=x-columns*n/2,top=y-rows*n,base=red?4:0;
  group.addChild(new Graphics().rect(left+2,top+5,columns*n,rows*n-3).fill({color:0x405b37,alpha:.2}));
  // 지붕은 두 줄의 좌/가운데/우 끝 조각만 반복한다.
  for(let row=0;row<2;row++)for(let col=0;col<columns;col++){
   sprite(group,left+col*n,top+row*n,n,base+(col===0?0:col===columns-1?2:1),4+row);
  }
  // 벽은 독립된 solid 셀. row7은 통째 밴드가 아니라 창/문 하나씩 얹는다.
  for(let row=2;row<rows;row++)for(let col=0;col<columns;col++)sprite(group,left+col*n,top+row*n,n,base+1,6);
  sprite(group,x-n/2,y-n,n,base+1,7);
  sprite(group,left+n/4,y-n,n,base,7);
  if(columns>3)sprite(group,left+(columns-1.25)*n,y-n,n,base,7);
  return group;
 };
 for(const [i,h] of map.homes.entries()){
  house(h.id,h.x,h.y,h.columns,h.n,i%2===1,h.height/h.n);
  cottageDetails(ground,h,i);
 }
 for(const actor of actors){
  const p=l.routes[actor],s=l.sites[actor];
  house(`building-${actor}`,s.x,s.y,p.columns,p.tileSize,p.red);
  const props=layer(`work-${actor}`,p.work.y-1);
  workDetails(props,actor,p.work);
 }
 for(const [i,t] of map.trees.entries()){
  const tree=new Container();tree.label=`tree-${i}`;tree.zIndex=t.y;world.addChild(tree);
  // 원본의 완결된 1열×2행: 주황3/초록4. 인접 수관5/9/10은 섞지 않는다.
  sprite(tree,t.x-t.size/2,t.y-2*t.size,t.size,t.autumn?3:4,0);
  sprite(tree,t.x-t.size/2,t.y-t.size,t.size,t.autumn?3:4,1);
 }
 return {...map,streamX:Math.round(l.width*.92),pondX:l.width*.87,pondY:l.height*.34};
}

/** 한 색의 연속 픽셀은 가로 띠로 묶어 draw-call/도형 수를 줄인다. */
function paintCells(g:Graphics,cells:Point[],color:number) {
 const rows=new Map<number,number[]>();
 for(const p of cells){if(!rows.has(p.y))rows.set(p.y,[]);rows.get(p.y)!.push(p.x);}
 for(const [y,xs] of rows){
  xs.sort((a,b)=>a-b);let start=xs[0],end=start;
  for(const x of [...xs.slice(1),Infinity]){
   if(x===end+1){end=x;continue;}
   g.rect(start,y,end-start+1,1);start=end=x;
  }
 }
 g.fill(color);
}
function flowers(g:Graphics,x:number,y:number,count:number) {
 for(let i=0;i<count;i++){
  const a=Math.round(x+i%3*4),b=Math.round(y+Math.floor(i/3)*4);
  g.rect(a,b,1,3).fill(0x5c7e44).rect(a-1,b,3,1).fill(i%2?0xe8cf89:0xe4dcb2);
 }
}
function bench(g:Graphics,x:number,y:number) {
 g.rect(x,y,13,3).fill(0x977349).rect(x,y-3,13,2).fill(0xb89260);
 g.rect(x+1,y+3,2,3).rect(x+10,y+3,2,3).fill(0x69513a);
}
function cottageDetails(g:Graphics,h:Home,i:number) {
 const left=h.x-h.width/2;
 // 짧은 울타리는 현관 양옆에만, 골목 종점과 문 사이에는 놓지 않는다.
 for(const side of [-1,1]){
  const x=h.x+side*(h.width/2-3);
  g.rect(x,h.y+5,2,7).fill(0x896b48).rect(x,h.y+5,2,2).fill(0xd6bc80);
 }
 g.rect(left+h.width-1,h.y+3,2,7).fill(0x795c40).rect(left+h.width-3,h.y+2,6,4).fill(i%2?0xab7356:0x7b9382);
 if(h.style==='porch')g.rect(left+2,h.y-2,h.width-4,2).fill(0xc4a36b);
 if(h.style==='gable')g.rect(left+h.width-6,h.y-h.height-2,3,5).fill(0x76685a);
 if(h.style==='long-roof')flowers(g,left-3,h.y+1,4);
}
function workDetails(g:Graphics,actor:Actor,p:Point) {
 const {x,y}=p;
 const rect=(a:number,b:number,w:number,h:number,c:number)=>g.rect(a,b,w,h).fill(c);
 if(actor==='researcher-1'){
  rect(x-8,y-12,15,11,0x785338);
  for(let i=0;i<5;i++)rect(x-6+i*2,y-10,1,7,[0xbb7457,0x9ca971,0xd6c18f][i%3]);
  rect(x-8,y-4,15,1,0xbd965c);
 }else if(actor==='researcher-2'){
  rect(x-8,y-8,16,7,0x8b6c43);
  for(let i=0;i<4;i++){rect(x-6+i*3,y-7,2,4,0x567a3d);rect(x-6+i*3,y-8,2,2,0xd8af5d);}
 }else if(actor==='researcher-4'){
  rect(x-7,y-8,13,7,0x684e39);rect(x-6,y-7,11,4,0x9db8aa);rect(x-1,y-11,2,4,0xc6b982);
 }else{
  rect(x-8,y-5,16,3,0x8c6749);rect(x-7,y-2,2,4,0x654f3b);rect(x+5,y-2,2,4,0x654f3b);
  if(actor==='editor'){
   rect(x-6,y-14,10,9,0x465455);rect(x-5,y-13,8,6,0x8bbab7);rect(x+5,y-8,5,5,0xe9dbb7);
  }else if(actor==='researcher-3'){
   rect(x-4,y-8,8,3,0xd2b17b);rect(x+2,y-11,2,6,0x626a60);rect(x,y-12,6,2,0xa6aea0);
  }else rect(x-3,y-7,7,2,0xf4eacb);
 }
}
