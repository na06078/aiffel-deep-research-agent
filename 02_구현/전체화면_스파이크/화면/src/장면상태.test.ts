import { it, expect } from 'vitest';
import {scenePhase,retryIdentity} from './장면상태';
it('각 합성 동작은 독립 실행되고 운반은 문서 제출에 도달한다',()=>{
 expect(scenePhase('move',0)).toBe('moving');
 expect(scenePhase('move',1500)).toBe('at-workstation');
 expect(scenePhase('work',0)).toBe('working');
 expect(scenePhase('work',1600)).toBe('worked');
 expect(scenePhase('deliver',0)).toBe('delivering');
 expect(scenePhase('deliver',2200)).toBe('submitted');
 expect(scenePhase('reset',9000)).toBe('idle');
});
it('동일 초안 재시도는 ID를 보존하고 다른 지시는 새 ID를 받는다',()=>{
 const prior={text:'A',id:'request-A'};
 expect(retryIdentity(prior,'A',()=> 'new')).toEqual(prior);
 expect(retryIdentity(prior,'B',()=> 'request-B')).toEqual({text:'B',id:'request-B'});
 expect(retryIdentity(null,'A',()=> 'fresh-A').id).toBe('fresh-A');
});
