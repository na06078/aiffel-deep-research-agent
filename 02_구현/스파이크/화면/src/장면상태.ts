export type Phase='idle'|'moving'|'at-workstation'|'working'|'worked'|'delivering'|'submitted';
export type SceneAction='move'|'work'|'deliver'|'reset';
export function scenePhase(action:SceneAction,elapsed:number):Phase {
 if(action==='move')return elapsed<1500?'moving':'at-workstation';
 if(action==='work')return elapsed<1600?'working':'worked';
 if(action==='deliver')return elapsed<2200?'delivering':'submitted';
 return 'idle';
}
export function retryIdentity(previous:{text:string;id:string}|null,text:string,make:()=>string){return previous?.text===text?previous:{text,id:make()};}
