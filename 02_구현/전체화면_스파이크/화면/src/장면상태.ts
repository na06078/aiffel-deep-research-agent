export type Phase='idle'|'moving'|'at-workstation'|'working'|'worked'|'delivering'|'submitted';
export type SceneAction='move'|'work'|'deliver'|'reset';
export function scenePhase(action:SceneAction,elapsed:number):Phase {
 if(action==='move')return elapsed<1500?'moving':'at-workstation';
 if(action==='work')return elapsed<1600?'working':'worked';
 if(action==='deliver')return elapsed<2200?'delivering':'submitted';
 return 'idle';
}
import type {SourceMode} from './계약';
export interface RetryCommand {text:string;id:string;source_mode?:SourceMode}
// A retry is the original payload, not a fresh interpretation of background selection.
export function retryIdentity(previous:RetryCommand|null,text:string,make:()=>string,source_mode?:SourceMode):RetryCommand{return previous?.text===text?previous:{text,id:make(),...(source_mode===undefined?{}:{source_mode})};}
