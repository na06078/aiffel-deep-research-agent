import {afterEach,expect,it,vi} from 'vitest';
afterEach(()=>vi.unstubAllGlobals());
import {projectArchive,offsetMs,sceneClock,stageTimes,replayEnd,measuredElapsed,adjacentStage} from './시간재생';
import type {RunArchive} from './시간계약';
import {projectionSnapshot} from './시간재생';
import {actorState} from './역할투영';
it('실제 재생은 제공자와 관측된 시도 수를 보존하고 gate 없이 모델 작업 중을 투영한다',()=>{
 const a:RunArchive={...archive,provider:'openai',events:[{...event(1,0,'instruction.received',null,{text:'Q'}),provider:null},{...event(2,100,'model.started'),provider:'openai',status:'started'},{...event(3,200,'model.completed','비밀 없는 출력'),provider:'openai'}]};
 const network=vi.fn();vi.stubGlobal('fetch',network);const frozen=JSON.stringify(a);
 const projection=projectArchive(a,150),snap=projectionSnapshot(projection);
 expect(snap.provider).toBe('openai');expect(snap.mode).toBe('research');expect(snap.actual_model_requests).toBe(1);expect(snap.mock_requests).toBe(0);
 expect(actorState('planner',snap,projection.run).status).toBe('running');expect(actorState('planner',snap,projection.run).summary).not.toContain('모의');
 expect(projectArchive(a,50).run.actual_model_requests).toBe(0);
 expect(actorState('planner',projectionSnapshot(projectArchive(a,250)),projectArchive(a,250).run).status).toBe('completed');
 expect(network).not.toHaveBeenCalled();expect(JSON.stringify(a)).toBe(frozen);
});
import type {EventDetail,RunSnapshot,Report} from './계약';
const iso=(ms:number)=>new Date(Date.UTC(2026,10,6)+ms).toISOString();
const run:RunSnapshot={run_id:'A',question:'最終 question',control_revision:1,status:'completed',phase:'submitted',plan:[{section_id:'s1',title:'plan',assignee:'researcher-1'}],draft:'final draft',report_id:'report-A',quality:{passed:true,reasons:[]},parent_report_id:null,created_at:iso(0),updated_at:iso(5000),mock_requests:2,actual_model_requests:0};
function event(seq:number,ms:number,type:string,output:unknown=null,input:unknown=null,revision=1):EventDetail{return {event_id:`e${seq}`,run_id:'A',seq,control_revision:revision,occurred_at:iso(ms),type,actor:'planner',recipient:null,status:'completed',summary:type,input,output,error:null,provider:'test-double'};}
const archive:RunArchive={protocol_version:1,provider:'test-double',run,revisions:[run],events:[event(1,100,'instruction.received',null,{text:'original question'}),event(2,1000,'plan.created',run.plan),event(3,3000,'draft.created','final draft'),event(4,4000,'quality.evaluated',run.quality),event(5,5000,'report.submitted',{report_id:'report-A',run_id:'A',control_revision:1,title:'report',markdown:'final draft',sha256:'hash',created_at:iso(5000),download_url:'/report'})],spans:[],started_at:iso(0),ended_at:iso(5000),recorded_at:iso(5000),duration_ms:5000,has_measured_spans:false,high_watermark_seq:5,truncated:false};
it('전체105개 사건을 역순 입력해도 seq 동률을 정렬하며 seek는 fetch·POST나 archive 변경 없이 반복된다',()=>{
 const network=vi.fn(()=>{throw new Error('seek must not call network');});vi.stubGlobal('fetch',network);
 const events=[event(1,0,'instruction.received',null,{text:'Q'}),...Array.from({length:103},(_,i)=>event(i+2,i+1,'model.completed',{summary:`result-${i}`})),event(105,104,'draft.created','last draft')];
 const full={...archive,events:events.reverse(),duration_ms:104};const frozen=JSON.stringify(full);
 expect(projectArchive(full,104).events).toHaveLength(105);expect(projectArchive(full,104).run.draft).toBe('last draft');
 expect(projectArchive(full,0).run.draft).toBeNull();expect(projectArchive(full,104).run.draft).toBe('last draft');
 const tied={...full,events:[event(2,0,'plan.created',[{section_id:'tie',title:'tie plan',assignee:'researcher-1'}]),event(1,0,'instruction.received',null,{text:'Q'})]};
 expect(projectArchive(tied,0).run.plan).toEqual([{section_id:'tie',title:'tie plan',assignee:'researcher-1'}]);
 expect(network).not.toHaveBeenCalled();expect(JSON.stringify(full)).toBe(frozen);
});
it.each([['model.failed','failed'],['execution.failed','failed'],['run.cancelled','cancelled'],['run.interrupted','interrupted']] as const)('%s는 성공 보고서를 만들지 않고 %s 상태로 남는다',(kind,status)=>{
 const a={...archive,events:[archive.events[0],event(6,2000,kind)]};expect(projectArchive(a,2500).run.status).toBe(status);expect(projectArchive(a,2500).report).toBeNull();
});
it('live의 이전 제출 문서는 새 실행 시작보다 앞선 실제 시각으로 유지하되 replay에는 끼워넣지 않는다',()=>{
 const previous={run_id:'previous-run',report_id:'previous-report',created_at:iso(-1000)} as Report;
 expect(sceneClock(archive,500,false,previous).report_submitted_ms).toBe(-1000);
 expect(sceneClock(archive,500,true,previous).report_submitted_ms).toBeUndefined();
 expect(sceneClock(archive,500,false,{...previous,created_at:'invalid'}).report_submitted_ms).toBeUndefined();
});
it('이전·다음 단계는1ms 차이 사건도 건너뛰지 않고 양끝에서 멈춘다',()=>{
 const a={...archive,events:[event(1,0,'instruction.received',null,{text:'Q'}),event(2,1,'plan.created',[]),event(3,2,'draft.created','D')],duration_ms:2};
 expect(adjacentStage(a,0,1)).toBe(1);expect(adjacentStage(a,1,1)).toBe(2);expect(adjacentStage(a,2,-1)).toBe(1);expect(adjacentStage(a,1,-1)).toBe(0);expect(adjacentStage(a,0,-1)).toBe(0);expect(adjacentStage(a,2,1)).toBe(2);
});
it('장면 clock과 단계 경계는 실제 사건 순서에서 결정되고 문서 운반은 별도2200ms이다',()=>{
 const a={...archive,events:[...archive.events,{...event(6,1100,'assignment.created',run.plan[0]),actor:'dispatcher',recipient:'researcher-1'},{...event(7,1200,'model.started',{gate_id:'g'},{assignment:run.plan[0]}),actor:'researcher-1',status:'waiting'}]};
 expect(sceneClock(a,1150,true)).toEqual({at_ms:1150,replay:true,actor_started_ms:{planner:100,'researcher-1':1100}});
 expect(sceneClock(a,4999,true).report_submitted_ms).toBeUndefined();
 expect(sceneClock(a,5000,true).report_submitted_ms).toBe(5000);
 expect(replayEnd(a)).toBe(7200);
 expect(stageTimes(a)).toEqual([0,100,1000,1100,1200,3000,4000,5000,7200]);
 expect(projectArchive(a,1300).waiting[0]?.gate_id).toBe('g');
 expect(sceneClock(a,1150,true)).toEqual(sceneClock(a,1150,true));
});
it('측정 구간의 합집합은 대기를 중복 가산하지 않고 null과 legacy는 미측정이다',()=>{
 const span=(id:string,start:number,end:number,kind:'node'|'model_wait')=>({span_id:id,run_id:'A',control_revision:1,actor:'planner',track_id:'planner',label:id,kind,started_at:iso(start),ended_at:iso(end),duration_ms:end-start,status:'completed' as const,timing_source:'monotonic' as const,event_ids:['e1']});
 const measured={...archive,has_measured_spans:true,spans:[span('n',0,1000,'node'),span('w',100,900,'model_wait'),span('n2',800,1500,'node')]};
 expect(measuredElapsed(measured)).toBe(1500);
 expect(measuredElapsed(archive)).toBeNull();
 expect(measuredElapsed({...measured,spans:[{...span('x',0,1000,'node'),ended_at:null,duration_ms:null}]})).toBeNull();
});
it('기록 시점 이전에는 최종 필드가 없고 역방향 탐색도 기준선에서 재구성한다',()=>{
 const before=projectArchive(archive,0) as any;
 expect(before.run).toMatchObject({question:'',plan:[],draft:null,report_id:null,quality:{passed:null,reasons:[]}});
 const early=projectArchive(archive,500) as any;
 expect(early.run).toMatchObject({question:'original question',plan:[],draft:null,report_id:null,status:'running'});
 expect((projectArchive(archive,1500) as any).run.plan).toEqual([{section_id:'s1',title:'plan',assignee:'researcher-1'}]);
 expect((projectArchive(archive,3500) as any).run).toMatchObject({draft:'final draft',report_id:null,quality:{passed:null,reasons:[]}});
 expect((projectArchive(archive,5500) as any).report.report_id).toBe('report-A');
 expect(projectArchive(archive,500)).toEqual(early);
});
it('새 instruction의 버전만 활성화하고 늦은 A 결과가 B에 누출되지 않는다',()=>{
 const b={...archive,events:[...archive.events.slice(0,3),event(6,3200,'instruction.received',null,{text:'B question'},2),event(7,3300,'draft.created','late A draft'),event(8,3400,'result.superseded',{report_id:'late-A'}),event(9,3500,'plan.created',[{section_id:'B1',title:'B plan',assignee:'researcher-2'}],null,2)]};
 const frozen=JSON.stringify(b);
 const state=projectArchive(b,3600);
 expect(state.run).toMatchObject({control_revision:2,question:'B question',draft:null,report_id:null,plan:[{section_id:'B1',title:'B plan',assignee:'researcher-2'}]});
 expect(state.report).toBeNull();expect(JSON.stringify(b)).toBe(frozen);
 projectArchive(b,100);expect(projectArchive(b,3600)).toEqual(state);
});
it('측정 불가능한 시각은0이 아니고 미래 보고서는 선택 시점 전에는 노출되지 않는다',()=>{
 expect(offsetMs(archive,'invalid')).toBeNull();expect(offsetMs(archive,iso(-1))).toBeNull();
 expect(projectArchive(archive,4999).report).toBeNull();
 expect(projectArchive(archive,5000).report?.report_id).toBe('report-A');
});
it('실시간 열린 단계와 대기는 관측 시점까지 한 번만 세고 완료 측정값은 만들지 않는다',()=>{
 const open={span_id:'open',run_id:'A',control_revision:1,actor:'planner',track_id:'planner',label:'plan',kind:'node' as const,started_at:iso(0),ended_at:null,duration_ms:null,status:'running' as const,timing_source:'monotonic' as const,event_ids:['e1']};
 const a={...archive,spans:[open,{...open,span_id:'wait',kind:'model_wait' as const,started_at:iso(500)}]};
 expect(measuredElapsed(a,3000)).toBe(3000);
 expect(measuredElapsed(a)).toBeNull();
 expect(a.spans.every(s=>s.duration_ms===null)).toBe(true);
});
