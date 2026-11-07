import {actors,type Point,type VillageLayout} from './마을배치';
export interface Box {x:number;y:number;width:number;height:number}
export interface Home extends Box {id:string;pocket:string;style:string;n:number;columns:number;lane:Point[]}
export interface Tree {x:number;y:number;size:number;autumn:boolean}
export interface VillageMap {trees:Tree[];homes:Home[];buildings:(Box&{id:string})[];roads:RoadMask;water:Box[];bridge:Box}
export function villageMap(l:VillageLayout):VillageMap {
 const compact=l.narrow||l.height<250, main=l.square.y;
 const buildings:(Box&{id:string})[]=actors.map(a=>{const p=l.routes[a],s=l.sites[a];return {id:a,x:s.x-p.columns*p.tileSize/2,y:s.y-4*p.tileSize,width:p.columns*p.tileSize,height:4*p.tileSize};});
 const riverHalf=l.narrow?7:13, riverBase=Math.round(l.width*.92);
 const water:Box[]=[],bends=[[0,-4],[.16,0],[.32,-10],[.50,0],[.72,-6],[1,3]];
 for(let y=0;y<l.height;y+=4){
  const t=y/l.height;
  const end=bends.findIndex(p=>p[0]>t),a=bends[end-1],b=bends[end];
  const bend=Math.round(a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0]));
  const pond=!compact&&t>.28&&t<.40?Math.round(28*Math.sqrt(1-((t-.34)/.06)**2)):0;
  water.push({x:riverBase+bend-riverHalf-pond,y,width:riverHalf*2+pond,height:4});
 }
 const crossing=water.filter(b=>b.y<=main+8&&b.y+4>=main-8);
 const left=Math.min(...crossing.map(b=>b.x)),right=Math.max(...crossing.map(b=>b.x+b.width));
 const bridge={x:left-6,y:main-8,width:right-left+12,height:16};
 const roads:Road[]=[{points:l.street,width:compact?8:14},...actors.flatMap(a=>[{points:l.routes[a].activity,width:6},{points:l.routes[a].delivery,width:6}])];
 const rx=l.narrow?25:compact?30:50,ry=l.narrow?19:compact?14:28;
 for(let y=main-ry;y<=main+ry;y++)roads.push({points:[{x:l.square.x-rx,y},{x:l.square.x+rx,y}],width:0});
 for(const a of actors)roads.push({points:[{x:l.sites[a].x,y:l.sites[a].y+1},l.routes[a].home],width:4});
 const actorRoad=rasterRoads(roads),homes:Home[]=[];
 const north=l.narrow?[[.12,.16],[.16,.25],[.60,.12],[.80,.15]]:compact?[[.08,.25],[.27,.24],[.57,.22],[.80,.23]]:[[.12,.17],[.30,.18],[.34,.34],[.60,.16],[.82,.18],[.08,.34]];
 const south=l.narrow?[[.12,.78],[.31,.92],[.79,.86],[.20,.89]]:compact?[[.08,.78],[.90,.79],[.43,.87],[.66,.90]]:[[.09,.72],[.15,.88],[.35,.92],[.65,.91],[.86,.82],[.07,.90]];
 for(const [group,candidates] of [north,south].entries()){
  let count=0;
  for(const [fx,fy] of candidates){
   if(count===(compact?2:3))break;
   const n=8,columns=count===1?4:3,x=Math.round(l.width*fx),y=Math.round(l.height*fy),width=columns*n,height=(compact?3:4)*n;
   const box={x:x-width/2,y:y-height,width,height};
   if(box.x<4||box.y<4||box.x+width>=l.width-4||y+12>=l.height)continue;
   if(buildings.some(b=>overlaps(box,b,8))||water.some(b=>overlaps(box,b,6)))continue;
   if(actorRoad.cells.some(p=>inside(p,box,8)))continue;
   const home:Home={id:`cottage-${homes.length}`,pocket:group?'garden-lane':'orchard-lane',style:['gable','long-roof','porch','lean-to'][homes.length%4],x,y,width,height,n,columns,lane:[]};
   homes.push(home);buildings.push({...box,id:home.id});count++;
  }
 }
 for(const home of homes){
  home.lane=doorLane({x:home.x,y:home.y+6},main,l,buildings,water);
  // 문턱만 좁게 접속하고 골목은 넓힌다. 확대해도 긴 선/기둥으로 읽히지 않게 한다.
  roads.push({points:[{x:home.x,y:home.y+1},home.lane[0]],width:4});
  roads.push({points:home.lane,width:6});
 }
 const mask=rasterRoads(roads),trees:Tree[]=[],size=compact?8:16;
 const groves=[[.025,.18],[.06,.32],[.035,.84],[.22,.97],[.43,.08],[.66,.10],[.84,.16],[.98,.37],[.98,.82],[.82,.97],[.67,.87],[.41,.94]];
 const offsets=[[0,0],[-16,-8],[14,-15],[-12,18],[16,17],[0,33],[-29,8]];
 const protectedPoints=[l.paper,...actors.flatMap(a=>[l.routes[a].home,l.routes[a].work])];
 for(const [i,center] of groves.entries())for(const [dx,dy] of offsets){
  const x=Math.round(l.width*center[0]+dx*size/16),y=Math.round(l.height*center[1]+dy*size/16);
  const box={x:x-size/2,y:y-2*size,width:size,height:2*size};
  if(box.x<0||box.y<0||box.x+size>l.width||y>l.height)continue;
  if([...buildings,...water,bridge].some(b=>overlaps(box,b,4)))continue;
  if(mask.cells.some(p=>inside(p,box,5))||protectedPoints.some(p=>inside(p,box,12)))continue;
  if(trees.some(t=>Math.abs(t.x-x)<size*.7&&Math.abs(t.y-y)<size))continue;
  trees.push({x,y,size,autumn:i===3||i===9});
 }
 return {trees,homes,buildings,roads:mask,water,bridge};
}

function inside(p:Point,b:Box,gap=0):boolean {
 return p.x>=b.x-gap&&p.x<=b.x+b.width+gap&&p.y>=b.y-gap&&p.y<=b.y+b.height+gap;
}
function overlaps(a:Box,b:Box,gap=0):boolean {
 return a.x<b.x+b.width+gap&&a.x+a.width>b.x-gap&&a.y<b.y+b.height+gap&&a.y+a.height>b.y-gap;
}
/** 문에서 가장 가까운 큰길까지, 지붕/마당/강변을 피하는 정수 격자 탐색. */
function doorLane(start:Point,main:number,l:VillageLayout,buildings:Box[],water:Box[]):Point[] {
 const w=l.width,h=l.height,total=w*h,previous=new Int32Array(total).fill(-1),queue=new Int32Array(total);
 const first=start.y*w+start.x;let head=0,tail=1,end=-1;queue[0]=first;previous[first]=first;
 const blocked=(p:Point)=>buildings.some(b=>inside(p,b,4))||water.some(b=>inside(p,b,4));
 while(head<tail){
  const id=queue[head++],x=id%w,y=Math.floor(id/w);
  if(y===main){end=id;break;}
  const vertical=main>y?1:-1;
  for(const [dx,dy] of [[0,vertical],[-1,0],[1,0],[0,-vertical]]){
   const p={x:x+dx,y:y+dy},next=p.y*w+p.x;
   if(p.x<3||p.x>=w-3||p.y<3||p.y>=h-12||previous[next]!==-1||blocked(p))continue;
   previous[next]=id;queue[tail++]=next;
  }
 }
 if(end<0)throw new Error('주거 골목이 큰길에 연결되지 않았습니다.');
 const reversed:Point[]=[];
 for(let id=end;;id=previous[id]){reversed.push({x:id%w,y:Math.floor(id/w)});if(id===first)break;}
 const points=reversed.reverse();
 return points.filter((p,i)=>i===0||i===points.length-1||(p.x-points[i-1].x)!==(points[i+1].x-p.x)||(p.y-points[i-1].y)!==(points[i+1].y-p.y));
}
export interface Road {points:Point[];width:number}
export interface RoadMask {cells:Point[];edge:Point[];contains:(p:Point)=>boolean}
/** 정수 픽셀 직사각형들의 합집합. 외곽만 이웃 검사하므로 합류부에 내부 선이 없다. */
export function rasterRoads(roads:Road[]):RoadMask {
 const occupied=new Set<string>(), key=(x:number,y:number)=>`${x},${y}`;
 for(const road of roads){
  const r=Math.floor(road.width/2);
  for(let i=1;i<road.points.length;i++){
   const a=road.points[i-1],b=road.points[i];
   if(a.x!==b.x&&a.y!==b.y) throw new Error('길은 직교해야 합니다.');
   for(let y=Math.min(a.y,b.y)-r;y<=Math.max(a.y,b.y)+r;y++){
    for(let x=Math.min(a.x,b.x)-r;x<=Math.max(a.x,b.x)+r;x++) occupied.add(key(x,y));
   }
  }
 }
 const cells=Array.from(occupied,k=>{const [x,y]=k.split(',').map(Number);return {x,y};});
 const contains=(p:Point)=>occupied.has(key(Math.round(p.x),Math.round(p.y)));
 const edge=cells.filter(p=>[[1,0],[-1,0],[0,1],[0,-1]].some(([dx,dy])=>!occupied.has(key(p.x+dx,p.y+dy))));
 return {cells,edge,contains};
}
