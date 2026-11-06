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
