import {expect,it} from 'vitest';
import {Container,Graphics,Texture} from 'pixi.js';
import {drawVillage} from './마을풍경';
import {actors,villageLayout} from './마을배치';
it('네 화면 크기에서 실제 Pixi 지형·건물 그래프를 조립하며 atlas 범위를 벗어나지 않는다',()=>{
 for(const [width,height] of [[1440,900],[1100,720],[390,844],[844,390]]){
  const l=villageLayout(width,height),world=new Container();
  const used:number[][]=[];
  drawVillage(world,l,(x,y)=>{used.push([x,y]);return Texture.EMPTY;});
  expect(world.children.length).toBeGreaterThan(30);
  for(const [x,y] of used){expect(x).toBeGreaterThanOrEqual(0);expect(x).toBeLessThan(12);expect(y).toBeGreaterThanOrEqual(0);expect(y).toBeLessThan(11);}
  expect(used.some(([x,y])=>x===0&&y===7)).toBe(true);
  expect(used.some(([x,y])=>x===4&&y===7)).toBe(true);
  for(const actor of actors)expect(world.children.some(c=>c instanceof Container&&!(c instanceof Graphics)&&c.zIndex===l.sites[actor].y)).toBe(true);
  world.destroy({children:true});
 }
});
it('집 두 동네와 장터 광장을 실제로 그리되 여섯 역할의 건물과 활동 지점을 막지 않는다',()=>{
 for(const [width,height] of [[1440,900],[1100,720],[390,844],[844,390]]){
  const l=villageLayout(width,height),world=new Container();
  const scene=drawVillage(world,l,()=>Texture.EMPTY);
  expect(scene).toHaveProperty('homes');
  const homes=(scene as typeof scene & {homes:{id:string;pocket:string;x:number;y:number;width:number;height:number;style:string}[]}).homes;
  expect(homes.length).toBe(l.narrow||l.height<220?4:6);
  expect(new Set(homes.map(h=>h.pocket)).size).toBe(2);
  expect(new Set(homes.map(h=>h.style)).size).toBeGreaterThanOrEqual(3);
  for(const h of homes){
   const building=world.children.find(c=>c.label===h.id);
   expect(building, h.id).toBeDefined();
   expect(building!.getLocalBounds().width).toBeGreaterThanOrEqual(h.width);
   expect(h.x-h.width/2).toBeGreaterThanOrEqual(0);
   expect(h.x+h.width/2).toBeLessThan(l.width);
   expect(h.y-h.height).toBeGreaterThanOrEqual(0);
   expect(h.y+10).toBeLessThan(l.height);
   for(const actor of actors){
    const p=l.routes[actor],s=l.sites[actor],half=p.columns*p.tileSize/2;
    const separated=h.x+h.width/2+2<s.x-half||h.x-h.width/2-2>s.x+half||h.y+3<s.y-4*p.tileSize||h.y-h.height-2>s.y;
    expect(separated, `${width}: ${h.id} / ${actor}`).toBe(true);
    for(const point of [p.home,p.work])expect(point.x<h.x-h.width/2-4||point.x>h.x+h.width/2+4||point.y<h.y-h.height-4||point.y>h.y+4,`${width}: ${h.id} / ${actor} @ ${point.x},${point.y}`).toBe(true);
   }
  }
  expect(world.children.some(c=>c.label==='market-square')).toBe(true);
  expect(world.children.filter(c=>c.label?.startsWith('market-stall-')).length).toBe(2);
  world.destroy({children:true});
 }
});
it('광장의 좌석과 화단은 포장 아래에 묻히지 않는 별도 전경이다',()=>{
 const world=new Container();drawVillage(world,villageLayout(1440,900),()=>Texture.EMPTY);
 const paving=world.children.find(c=>c.label==='market-square')!;
 const furnishings=world.children.find(c=>c.label==='square-furnishings');
 expect(furnishings).toBeDefined();
 expect(furnishings!.zIndex).toBeGreaterThan(paving.zIndex);
 expect(furnishings!.getLocalBounds().width).toBeGreaterThan(70);
 world.destroy({children:true});
});
