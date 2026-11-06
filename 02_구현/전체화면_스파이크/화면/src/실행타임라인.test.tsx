import {expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {Timeline} from './실행타임라인';
import type {TimelineProps} from './실행타임라인';
import type {RunArchive} from './시간계약';
const archive:RunArchive={protocol_version:1,provider:'test-double',run:{run_id:'A',question:'saved question',control_revision:1,status:'completed',phase:'submitted',plan:[],draft:null,report_id:null,quality:{passed:null,reasons:[]},created_at:'2026-11-06T00:00:00Z',updated_at:'2026-11-06T00:00:02Z',parent_report_id:null,mock_requests:0,actual_model_requests:0},revisions:[],events:[{event_id:'real-event',run_id:'A',control_revision:1,seq:1,occurred_at:'2026-11-06T00:00:00Z',type:'instruction.received',actor:'planner',recipient:null,status:'received',summary:'지시',input:{text:'saved question'},output:null,error:null,provider:'test-double'}],spans:[{span_id:'real-span',run_id:'A',control_revision:1,actor:'planner',track_id:'planner',label:'계획 노드',kind:'node',started_at:'2026-11-06T00:00:00Z',ended_at:'2026-11-06T00:00:02Z',duration_ms:2000,status:'completed',timing_source:'monotonic',event_ids:['real-event']}],started_at:'2026-11-06T00:00:00Z',ended_at:'2026-11-06T00:00:02Z',recorded_at:'2026-11-06T00:00:02Z',duration_ms:2000,has_measured_spans:true,truncated:false,high_watermark_seq:1};
it('진행 중 미종료 구간은 미측정을 유지하며 현재 경과를 별도 표시한다',()=>{
 const pending={...archive,run:{...archive.run,status:'running' as const},ended_at:null,spans:[{...archive.spans[0],ended_at:null,duration_ms:null,status:'running' as const}]};
 const html=renderToStaticMarkup(<Timeline {...base} archive={pending} at={3000}/>);
 expect(html).toContain('현재 경과 3.00초');expect(html).toContain('미측정');expect(html).not.toContain('전체 · 0.00초');
});
it('객체별 실제 구간과 정확한 시간축·scrubber를 제공하고 legacy 구간은 만들지 않는다',()=>{
 const html=renderToStaticMarkup(<Timeline {...base} archive={archive} runs={[archive.run]} at={1000} replay/>);
 for(const id of ['planner','researcher-1','researcher-2','researcher-3','researcher-4','editor','code','document'])expect(html).toContain(`data-testid="timeline-track-${id}"`);
 expect(html).toContain('data-testid="timeline-span-real-span"');expect(html).toContain('2.00초');
 expect(html).toContain('data-testid="timeline-scrubber"');expect(html).toContain('실제 경과');expect(html).not.toContain('% 완료');
 const legacy=renderToStaticMarkup(<Timeline {...base} archive={{...archive,spans:[],has_measured_spans:false}} runs={[archive.run]}/>);
 expect(legacy).toContain('미측정');expect(legacy).not.toContain('timeline-span-real-span');expect(legacy).toContain('real-event');
});
const base:TimelineProps={open:true,runs:[],runId:null,archive:null,at:0,replay:false,playing:false,loading:false,error:'',onToggle:vi.fn(),onSelect:vi.fn(),onSeek:vi.fn(),onPlay:vi.fn(),onStage:vi.fn(),onLive:vi.fn(),onRetry:vi.fn(),onEvent:vi.fn()};
it('단계 전체와 응답 대기를 범례·이름·객체별 경과로 구분한다',()=>{
 const wait={...archive.spans[0],span_id:'wait',kind:'model_wait' as const,label:'model',duration_ms:1500};
 const html=renderToStaticMarkup(<Timeline {...base} archive={{...archive,spans:[archive.spans[0],wait]}}/>);
 expect(html).toContain('단계 전체');expect(html).toContain('응답 대기');expect(html).toContain('운반 연출');
 expect(html).toContain('data-testid="timeline-total-planner"');
 expect(html).not.toContain('<span>model</span>');
});
it('하단 손잡이와 선택·재생 제어를 보존하고 기록이 없으면 정직하게 안내한다',()=>{
 const html=renderToStaticMarkup(<Timeline {...base}/>);
 for(const id of ['timeline-toggle','timeline-drawer','timeline-run-select','replay-play','replay-prev','replay-next','replay-live','replay-status','replay-time'])expect(html).toContain(`data-testid="${id}"`);
 expect(html).toContain('보존된 실행이 없습니다');
 expect(renderToStaticMarkup(<Timeline {...base} open={false}/>)).not.toContain('data-testid="timeline-drawer"');
});
it('실제 제공자의 열린 실시간 타임라인에 모의 응답이라고 표시하지 않는다',()=>{
 const html=renderToStaticMarkup(<Timeline {...base} archive={{...archive,provider:'openai'}}/>);
 expect(html).toContain('실시간');
 expect(html).not.toContain('실시간 · 모의 응답');
});
