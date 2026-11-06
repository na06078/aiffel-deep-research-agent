import {expect,it} from 'vitest';
import {actorState} from './역할투영';
import type {Snapshot,RunSnapshot} from './계약';
it('현재 gate만 대기로 표시하고 이전 완료나 늦은 gate는 현재 NPC에 섞지 않는다',()=>{
 const run={run_id:'B',control_revision:2,status:'running'} as RunSnapshot;
 const s={waiting:[{actor:'researcher-1',run_id:'B',control_revision:2,is_stale:false},{actor:'researcher-2',run_id:'B',control_revision:1,is_stale:true}],events:[{event_id:'old',seq:100,actor:'editor',run_id:'A',control_revision:2,type:'draft.created'},{event_id:'now',seq:5,actor:'researcher-1',run_id:'B',control_revision:2,type:'model.started',status:'waiting',summary:'대기'}]} as Snapshot;
 expect(actorState('researcher-1',s,run).status).toBe('waiting');
 expect(actorState('researcher-2',s,run).status).toBe('idle');
 expect(actorState('editor',s,run).event).toBeUndefined();
 expect(actorState('researcher-1',s,{...run,status:'cancelled'}).status).toBe('cancelled');
 expect(actorState('editor',null,undefined).status).toBe('idle');
});
it('늦은 결과는 같은 버전에서도 현재 작업 말풍선으로 투영하지 않는다',()=>{
 const run={run_id:'B',control_revision:2,status:'running'} as RunSnapshot;
 const s={waiting:[],events:[{event_id:'good',seq:3,actor:'researcher-1',run_id:'B',control_revision:2,type:'model.completed',status:'completed'},{event_id:'stale',seq:4,actor:'researcher-1',run_id:'B',control_revision:2,type:'result.superseded',status:'superseded'}]} as unknown as Snapshot;
 expect(actorState('researcher-1',s,run).event?.event_id).toBe('good');
});
it('내부 사건명 대신 실제 질문·배정 제목과 관측 상태를 말풍선에 표시한다',()=>{
 const run={run_id:'B',control_revision:2,status:'running',question:'도시 숲의 효과',plan:[{assignee:'researcher-1',title:'기온 완화',section_id:'s1'}]} as RunSnapshot;
 const s={waiting:[{actor:'researcher-1',run_id:'B',control_revision:2,is_stale:false}],events:[{event_id:'p',seq:1,actor:'planner',run_id:'B',control_revision:2,type:'plan.created',status:'completed',summary:'plan.created'},{event_id:'r',seq:2,actor:'researcher-1',run_id:'B',control_revision:2,type:'model.started',status:'waiting',summary:'model.started'}]} as Snapshot;
 expect((actorState('planner',s,run) as {summary?:string}).summary).toContain('도시 숲의 효과');
 expect((actorState('researcher-1',s,run) as {summary?:string}).summary).toBe('기온 완화 · 모의 응답 대기');
});
