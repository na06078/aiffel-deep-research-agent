import {expect,it} from 'vitest';
import {actors,villageLayout} from './마을배치';
it('세계의 논리 크기는 viewport를 채우고 여섯 작업장은 좁은 화면에서도 서로 구별된다',()=>{
 for(const [w,h] of [[1440,900],[1100,720],[390,844],[844,390]]){
  const l=villageLayout(w,h);expect(Number.isInteger(l.scale)).toBe(true);
  expect(l.width*l.scale).toBeGreaterThanOrEqual(w);expect(l.height*l.scale).toBeGreaterThanOrEqual(h);
  expect(l.width*l.scale-w).toBeLessThan(l.scale);expect(l.height*l.scale-h).toBeLessThan(l.scale);
  const points=actors.map(a=>l.sites[a]);expect(new Set(points.map(p=>`${p.x},${p.y}`)).size).toBe(6);
  for(const p of points){expect(p.x*l.scale).toBeGreaterThan(32);expect(p.x*l.scale).toBeLessThan(w-32);expect(p.y*l.scale).toBeLessThan(h-95);}
 }
 expect(villageLayout(390,844).sites.editor.y).toBeGreaterThan(villageLayout(390,844).sites['researcher-2'].y);
 expect(()=>villageLayout(0,900)).toThrow('잘못된 화면 크기');
});
it('장소는 같은 행의 업무 격자가 아니라 서로 다른 높이의 마을 동네다',()=>{
 const l=villageLayout(1440,900);
 expect(new Set(actors.map(a=>l.sites[a].y)).size).toBeGreaterThanOrEqual(5);
});
it('여섯 장소는 고유한 활동 지점에서 연결된 길을 따라 광장까지 이어진다',()=>{
 const l=villageLayout(1440,900) as ReturnType<typeof villageLayout> & {routes?:Record<string,{home:{x:number;y:number};work:{x:number;y:number};activity:{x:number;y:number}[];delivery:{x:number;y:number}[]}>};
 expect(l.routes).toBeDefined();
 for(const actor of actors){
  const route=l.routes![actor];
  expect(route.activity[0]).toEqual(route.home);
  expect(route.activity.at(-1)).toEqual(route.work);
  expect(route.delivery[0]).toEqual(route.work);
  expect(route.delivery.at(-1)).toEqual(l.paper);
  expect(route.delivery.length).toBeGreaterThanOrEqual(4);
 }
});
