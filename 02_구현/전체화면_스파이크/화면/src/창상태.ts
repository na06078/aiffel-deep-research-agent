export type Menu = '장면 도구'|'실행/큐'|'과거 기록'|'사건 기록'|'제어 도구'|'안내';
export type Popup = {kind:Menu}|{kind:'event';id:string;local:boolean}|{kind:'history';runId:string}|{kind:'report';id:string;local?:boolean}|{kind:'idle';actor:string};
export interface UIState {drawer:boolean;popup:Popup|null;draft:string;mode:'assets'|'control'}
export type UIAction = {type:'drawer'}|{type:'close'}|{type:'open';popup:Popup}|{type:'back'}|{type:'draft';text:string}|{type:'mode';mode:UIState['mode']};
export const initialUI:UIState={drawer:false,popup:null,draft:'',mode:'assets'};
export function uiReducer(state:UIState,action:UIAction):UIState {
 switch(action.type){
  case 'drawer':return {...state,drawer:!state.drawer};
  case 'open':return {...state,drawer:false,popup:action.popup};
  case 'close':return {...state,drawer:false,popup:null};
  case 'draft':return {...state,draft:action.text};
  case 'mode':return {...state,mode:action.mode};
  case 'back':return state.popup?.kind==='history'?{...state,popup:{kind:'과거 기록'}}:state;
 }
}
