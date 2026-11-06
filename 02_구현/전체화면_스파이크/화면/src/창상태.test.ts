import {expect,it} from 'vitest';
import {initialUI,uiReducer} from './창상태';
it('햄버거에서 하나의 창을 열고 닫아도 작성 중인 지시는 유지한다',()=>{
 const start={...initialUI,draft:'아직 보내지 않은 지시'};
 const drawer=uiReducer(start,{type:'drawer'});expect(drawer.drawer).toBe(true);
 const opened=uiReducer(drawer,{type:'open',popup:{kind:'과거 기록'}});
 expect(opened.drawer).toBe(false);expect(opened.popup).toEqual({kind:'과거 기록'});
 const closed=uiReducer(opened,{type:'close'});expect(closed.popup).toBeNull();expect(closed.draft).toBe(start.draft);
});
it('과거 기록 상세에서 같은 창의 목록으로 돌아오고 모드 전환도 선택 사건을 바꾸지 않는다',()=>{
 let s=uiReducer({...initialUI,draft:'보존'}, {type:'open',popup:{kind:'history',runId:'run-A'}});
 s=uiReducer(s,{type:'back'});expect(s.popup).toEqual({kind:'과거 기록'});
 s=uiReducer(s,{type:'open',popup:{kind:'event',id:'event-A',local:false}});
 const next=uiReducer(s,{type:'mode',mode:'control'});
 expect(next.popup).toEqual({kind:'event',id:'event-A',local:false});expect(next.draft).toBe('보존');
 expect(uiReducer(next,{type:'back'}).popup).toEqual(next.popup);
});
