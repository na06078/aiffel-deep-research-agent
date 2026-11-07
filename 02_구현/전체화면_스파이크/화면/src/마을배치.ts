export const actors = ['planner', 'researcher-1', 'researcher-2', 'researcher-3', 'researcher-4', 'editor'] as const;
export type Actor = typeof actors[number];
export const names: Record<Actor, string> = {planner:'조정자','researcher-1':'조사관 1','researcher-2':'조사관 2','researcher-3':'조사관 3','researcher-4':'조사관 4',editor:'종합자'};
export interface Point {x:number;y:number}
export interface Place {title:string;columns:number;tileSize:number;red:boolean;home:Point;work:Point;activity:Point[];delivery:Point[]}
export interface VillageLayout {scale:number;width:number;height:number;sites:Record<Actor,Point>;paper:Point;routes:Record<Actor,Place>;street:Point[];square:Point;narrow:boolean}

/** 건물 발밑과 문 앞 동선이 정본이다. 지붕이나 강을 가로지르는 지름길은 만들지 않는다. */
export function villageLayout(width:number,height:number):VillageLayout {
 if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0) throw new Error('잘못된 화면 크기');
 const scale=2, w=Math.ceil(width/scale), h=Math.ceil(height/scale);
 const narrow=width<600, short=height<500, n=narrow||short?8:16;
 const bottom=Math.max(100,h-50), mainY=Math.round(bottom*(narrow?.49:short?.51:.54));
 const at=(x:number,y:number):Point=>({x:Math.round(w*x),y:Math.round(bottom*y)});
 const coords=narrow?[[.40,.20],[.22,.40],[.73,.31],[.26,.67],[.72,.63],[.55,.86]]
  :short?[[.43,.37],[.16,.39],[.70,.35],[.27,.83],[.79,.85],[.55,.88]]
  :[[.46,.32],[.19,.43],[.72,.29],[.30,.80],[.77,.82],[.55,.88]];
 const sites=Object.fromEntries(actors.map((a,i)=>[a,at(coords[i][0],coords[i][1])])) as Record<Actor,Point>;
 const square={x:Math.round(w*.51),y:mainY},paper={x:square.x+7,y:square.y+5};
 const street=[{x:0,y:mainY},square,{x:w-1,y:mainY}];
 const titles=['마을회관','숲속 자료실','씨앗 정원','목공 작업실','물가 관측소','우편 · 편집소'];
 const columns=narrow||short?[3,3,3,3,3,3]:[5,4,3,4,3,5];
 const sides=narrow?[1,-1,-1,-1,-1,-1]:[-1,-1,1,-1,-1,1];
 const routes=Object.fromEntries(actors.map((actor,i)=>{
  const site=sites[actor],home={x:site.x,y:site.y+6};
  const work={x:Math.round(site.x+sides[i]*(columns[i]*n/2+10)),y:site.y+14};
  const corner={x:home.x,y:work.y},junction={x:work.x,y:mainY};
  return [actor,{title:titles[i],columns:columns[i],tileSize:n,red:[false,false,true,true,false,true][i],home,work,
   activity:[home,corner,work],delivery:[work,junction,square,{x:paper.x,y:square.y},paper]}];
 })) as Record<Actor,Place>;
 return {scale,width:w,height:h,sites,paper,routes,street,square,narrow};
}
