export const actors=['planner','researcher-1','researcher-2','researcher-3','researcher-4','editor'] as const;
export type Actor=typeof actors[number];
export const names:Record<Actor,string>={planner:'조정자','researcher-1':'조사관 1','researcher-2':'조사관 2','researcher-3':'조사관 3','researcher-4':'조사관 4',editor:'종합자'};
export interface Point {x:number;y:number}
export interface Place { title:string; columns:number; tileSize:number; red:boolean; home:Point; work:Point; activity:Point[]; delivery:Point[] }
export interface VillageLayout { scale:number;width:number;height:number;sites:Record<Actor,Point>;paper:Point;routes:Record<Actor,Place>;street:Point[];square:Point;narrow:boolean }
export function villageLayout(width:number,height:number):VillageLayout{
 if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)throw new Error('잘못된 화면 크기');
 const scale=width<600||height<500?2:3;
 const w=Math.ceil(width/scale),h=Math.ceil(height/scale),bottom=Math.max(70,h-100/scale);
 const narrow=width<600;
 const coordinates=narrow?[[.39,.20],[.24,.39],[.73,.30],[.26,.67],[.76,.60],[.64,.82]]:[[.43,.31],[.19,.43],[.73,.25],[.32,.72],[.81,.64],[.59,.78]];
 const at=(x:number,y:number):Point=>({x:Math.round(w*x),y:Math.round(bottom*y)});
 const sites=Object.fromEntries(actors.map((a,i)=>[a,at(coordinates[i][0],coordinates[i][1])])) as Record<Actor,Point>;
 const square=at(.52,.51),paper={x:square.x+7,y:square.y+5};
 const street=narrow?[at(.08,.47),at(.35,.45),square,at(.65,.59),at(.88,.72)]:[at(.02,.54),at(.24,.51),at(.40,.46),square,at(.65,.56),at(.80,.53),at(.99,.48)];
 const titles=['마을회관','숲속 자료실','씨앗 정원','목공 작업실','물가 관측소','우편 · 편집소'];
 const offsets=narrow?[[16,13],[-16,16],[14,16],[-16,17],[12,16],[-17,16]]:[[30,17],[-28,20],[30,15],[-29,21],[29,18],[-32,19]];
 const routes=Object.fromEntries(actors.map((actor,i)=>{
  const site=sites[actor],home={x:site.x,y:site.y+6},work={x:site.x+offsets[i][0],y:site.y+offsets[i][1]};
  const elbow={x:home.x,y:work.y};
  // 하나의 길망에 붙은 동네별 진입로. 집을 가로지르는 직선 운반을 피한다.
  const junction=street.reduce((best,p)=>Math.hypot(p.x-work.x,p.y-work.y)<Math.hypot(best.x-work.x,best.y-work.y)?p:best,street[0]);
  const j=street.indexOf(junction),s=street.indexOf(square),segment=j<=s?street.slice(j,s+1):street.slice(s,j+1).reverse();
  return [actor,{title:titles[i],columns:[5,4,3,4,3,5][i],tileSize:narrow?8:height<500?9:12,red:[false,false,true,true,false,true][i],home,work,activity:[home,elbow,work],delivery:[work,{x:work.x,y:junction.y},...segment,paper]}];
 })) as Record<Actor,Place>;
 return {scale,width:w,height:h,sites,paper,routes,street,square,narrow};
}
