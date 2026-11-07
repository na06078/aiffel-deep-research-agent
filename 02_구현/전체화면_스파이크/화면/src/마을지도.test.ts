import {expect,it} from 'vitest';
import {rasterRoads,villageMap} from './마을지도';
import {actors,villageLayout} from './마을배치';
it('반응형 지도는 모든 집 문을 하나의 길망에 연결하고 강은 정렬된 다리 한 곳에서만 건넌다',()=>{
 for(const [width,height] of [[1440,900],[1100,720],[1000,700],[1920,1080],[390,844],[844,390],[640,360]]){
  const l=villageLayout(width,height),m=villageMap(l);
  expect(m.homes,`${width}: homes ${JSON.stringify(m.homes)}`).toHaveLength(l.narrow||l.height<250?4:6);
  expect(m.bridge.y+m.bridge.height/2).toBe(l.street[0].y);
  const crossing=m.water.find(w=>w.y<=l.street[0].y&&w.y+w.height>l.street[0].y)!;
  expect(m.bridge.x).toBeLessThan(crossing.x);
  expect(m.bridge.x+m.bridge.width).toBeGreaterThan(crossing.x+crossing.width);
  for(const path of [...actors.flatMap(a=>[l.routes[a].activity,l.routes[a].delivery]),...m.homes.map(h=>h.lane)]){
   for(let i=1;i<path.length;i++)for(let step=0;step<=30;step++){
    const a=path[i-1],b=path[i],p={x:Math.round(a.x+(b.x-a.x)*step/30),y:Math.round(a.y+(b.y-a.y)*step/30)};
    expect(m.roads.contains(p),`${width}: missing road ${JSON.stringify(p)}`).toBe(true);
    for(const box of m.buildings)expect(p.x>box.x-2&&p.x<box.x+box.width+2&&p.y>box.y-2&&p.y<box.y+box.height+2,`${width}: road hits ${box.id}`).toBe(false);
   }
  }
  for(const p of m.roads.cells){
   const wet=m.water.some(w=>p.x>=w.x&&p.x<w.x+w.width&&p.y>=w.y&&p.y<w.y+w.height);
   if(wet)expect(p.x>=m.bridge.x&&p.x<=m.bridge.x+m.bridge.width&&Math.abs(p.y-l.square.y)<=m.bridge.height/2,`${width}: wet road ${JSON.stringify(p)}`).toBe(true);
  }
 }
});
it('현관의 문턱까지 골목이 닿고 좁은 화면에서도 다리 양 끝에 보이는 육지가 남는다',()=>{
 for(const [width,height] of [[1440,900],[1100,720],[390,844],[844,390],[640,360]]){
  const l=villageLayout(width,height),m=villageMap(l);
  for(const a of actors)expect(m.roads.contains({x:l.sites[a].x,y:l.sites[a].y+1}),`${width}: ${a} doorstep`).toBe(true);
  for(const home of m.homes)expect(m.roads.contains({x:home.x,y:home.y+1})).toBe(true);
  expect(m.bridge.x).toBeGreaterThan(2);
  expect(m.bridge.x+m.bridge.width).toBeLessThan(l.width-2);
 }
});
it('숲은 무리로 저작하되 나무의 전체 실루엣을 길·건물·물·업무·제출에서 비운다',()=>{
 for(const [width,height] of [[1440,900],[1100,720],[390,844],[844,390],[640,360]]){
  const l=villageLayout(width,height),m=villageMap(l);
  expect(m.trees.length,`${width}: forest`).toBeGreaterThan(10);
  expect(m.trees.length).toBeLessThan(100);
  for(const t of m.trees){
   const b={x:t.x-t.size/2,y:t.y-t.size*2,width:t.size,height:t.size*2};
   expect(b.x).toBeGreaterThanOrEqual(0);expect(b.y).toBeGreaterThanOrEqual(0);
   expect(b.x+b.width).toBeLessThanOrEqual(l.width);expect(t.y).toBeLessThanOrEqual(l.height);
   for(const o of [...m.buildings,...m.water,m.bridge])expect(b.x<o.x+o.width+3&&b.x+b.width>o.x-3&&b.y<o.y+o.height+3&&b.y+b.height>o.y-3).toBe(false);
   expect(m.roads.cells.some(p=>p.x>=b.x-4&&p.x<=b.x+b.width+4&&p.y>=b.y-4&&p.y<=b.y+b.height+4)).toBe(false);
   for(const p of [l.paper,...actors.flatMap(a=>[l.routes[a].home,l.routes[a].work])])expect(p.x>b.x-12&&p.x<b.x+b.width+12&&p.y>b.y-12&&p.y<b.y+b.height+12).toBe(false);
  }
 }
});
it('물줄기의 저작된 굽이는 연못 밖에서 한 단마다 두 픽셀 이내로 이어진다',()=>{
 for(const [width,height] of [[1440,900],[390,844],[844,390]]){
  const l=villageLayout(width,height),m=villageMap(l);
  for(let i=1;i<m.water.length;i++){
   const a=m.water[i-1],b=m.water[i];
   expect(b.y).toBe(a.y+a.height);
   if(b.y/l.height<.27||a.y/l.height>.41)expect(Math.abs(a.x-b.x)).toBeLessThanOrEqual(2);
  }
 }
});
it('중심선만이 아니라 포장 전체 폭이 건물 안으로 들어가지 않는다',()=>{
 for(const [width,height] of [[1440,900],[1100,720],[1000,700],[1920,1080],[390,844],[844,390],[640,360]]){
  const l=villageLayout(width,height),m=villageMap(l);
  for(const box of m.buildings){
   const collision=m.roads.cells.find(p=>{
    const inBuilding=p.x>=box.x&&p.x<box.x+box.width&&p.y>=box.y&&p.y<box.y+box.height;
    const doorstep=p.y>=box.y+box.height-1&&Math.abs(p.x-(box.x+box.width/2))<=3;
    return inBuilding&&!doorstep;
   });
   expect(collision,`${width}: paving hits ${box.id}`).toBeUndefined();
  }
 }
});
it('주거 골목은 선처럼 보이지 않는 일곱 논리 픽셀의 보행 폭을 갖는다',()=>{
 const l=villageLayout(1440,900),m=villageMap(l);
 // 현관의 짧은 문턱은 제외하고 독립적으로 고른 긴 직선 구간의 양옆을 검사한다.
 for(const home of m.homes){
  const segment=home.lane.slice(1).map((p,i)=>[home.lane[i],p]).find(([a,b])=>Math.abs(a.x-b.x)+Math.abs(a.y-b.y)>=24);
  expect(segment,home.id).toBeDefined();
  const [a,b]=segment!;
  const mid={x:Math.round((a.x+b.x)/2),y:Math.round((a.y+b.y)/2)};
  for(const side of [-3,0,3])expect(m.roads.contains({x:mid.x+(a.x===b.x?side:0),y:mid.y+(a.y===b.y?side:0)}),`${home.id}: lane width ${side}`).toBe(true);
 }
});
it('T자·십자·모서리는 먼저 합집합하여 만나는 안쪽에 테두리가 생기지 않는다',()=>{
 const mask=rasterRoads([{points:[{x:0,y:10},{x:20,y:10}],width:6},{points:[{x:10,y:0},{x:10,y:20}],width:6}]);
 // 독립적인 두 직사각형의 합집합: 겹친 7×7은 한 번만 차지한다.
 expect(mask.cells).toHaveLength(27*7*2-7*7);
 expect(new Set(mask.cells.map(p=>`${p.x},${p.y}`)).size).toBe(mask.cells.length);
 for(const p of [{x:10,y:10},{x:7,y:10},{x:10,y:7},{x:13,y:10}]){
  expect(mask.contains(p)).toBe(true);
  expect(mask.edge.some(e=>e.x===p.x&&e.y===p.y)).toBe(false);
 }
 expect(mask.edge.some(p=>p.x===7&&p.y===0)).toBe(true);
 expect(mask.contains({x:6,y:6})).toBe(false);
 const corner=rasterRoads([{points:[{x:0,y:0},{x:10,y:0},{x:10,y:10}],width:4}]);
 expect(corner.contains({x:10,y:0})).toBe(true);
 expect(corner.edge.some(p=>p.x===10&&p.y===0)).toBe(false);
});
