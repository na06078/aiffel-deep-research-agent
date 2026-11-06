export const actors=['planner','researcher-1','researcher-2','researcher-3','researcher-4','editor'] as const;
export type Actor=typeof actors[number];
export const names:Record<Actor,string>={planner:'조정자','researcher-1':'조사관 1','researcher-2':'조사관 2','researcher-3':'조사관 3','researcher-4':'조사관 4',editor:'종합자'};
export interface Point {x:number;y:number}
export function villageLayout(width:number,height:number):{scale:number;width:number;height:number;sites:Record<Actor,Point>;paper:Point}{
 if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)throw new Error('잘못된 화면 크기');
 const scale=width<600?2:height<500?2:width<1200?3:4;
 const w=Math.ceil(width/scale),h=Math.ceil(height/scale),bottom=Math.max(70,h-100/scale);
 const narrow=width<600;
 const coordinates=narrow?[[.32,.20],[.72,.32],[.28,.44],[.72,.57],[.28,.69],[.68,.83]]:[[.5,.34],[.21,.29],[.79,.29],[.21,.69],[.79,.69],[.5,.71]];
 const sites=Object.fromEntries(actors.map((a,i)=>[a,{x:Math.round(w*coordinates[i][0]),y:Math.round(bottom*coordinates[i][1])}])) as Record<Actor,Point>;
 return {scale,width:w,height:h,sites,paper:{x:Math.round(w*.5),y:Math.round(bottom*.89)}};
}
