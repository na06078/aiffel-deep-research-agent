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
it('재생·시점·시간축은 고정 조작부에 있고 행 전용 스크롤에는 트랙만 있다',()=>{
 const html=renderToStaticMarkup(<Timeline {...base} archive={archive}/>);
 const toolbar=html.split('data-testid="timeline-toolbar"')[1]?.split('data-testid="timeline-track-scroll"')[0]??'';
 for(const id of ['timeline-run-select','replay-prev','replay-play','replay-next','replay-live','replay-time','timeline-scrubber'])expect(toolbar).toContain(`data-testid="${id}"`);
 expect(toolbar).toContain('timeline-axis');
 expect(toolbar).not.toContain('timeline-track-planner');
 const rows=html.split('data-testid="timeline-track-scroll"')[1]??'';
 expect(rows).toContain('timeline-track-document');expect(rows).not.toContain('timeline-scrubber');expect(rows).not.toContain('timeline-controls');
 expect(html.match(/data-testid="timeline-scrubber"/g)).toHaveLength(1);
});
it('상단 경계는 포커스 가능한 수평 separator로 높이와 키보드 조작을 설명한다',()=>{
 const html=renderToStaticMarkup(<Timeline {...base} height={360} minHeight={252} maxHeight={738} onHeightChange={vi.fn()}/>);
 const handle=html.split('data-testid="timeline-resize-handle"')[1]?.split('>')[0]??'';
 for(const attr of ['role="separator"','tabindex="0"','aria-orientation="horizontal"','aria-valuenow="360"','aria-valuemin="252"','aria-valuemax="738"','aria-controls="timing-drawer"'])expect(handle).toContain(attr);
 expect(handle).toContain('타임라인 높이');
});
it('실제 경계 핸들러는 포인터를 캡처하고 같은 포인터만 이동·종료하며 취소 후 재생을 건드리지 않는다',()=>{
 let handle:any;
 const changed=vi.fn(),seek=vi.fn(),play=vi.fn(),stage=vi.fn(),toggle=vi.fn();
 function find(node:any):any {if(!node||typeof node!=='object')return null;if(node.props?.['data-testid']==='timeline-resize-handle')return node;return [node.props?.children].flat(Infinity).map(find).find(Boolean);}
 function Probe(){const tree=Timeline({...base,height:360,minHeight:252,maxHeight:738,onHeightChange:changed,onSeek:seek,onPlay:play,onStage:stage,onToggle:toggle});handle=find(tree).props;return tree;}
 renderToStaticMarkup(<Probe/>);
 let captured:number|null=null;
 const target={focus:vi.fn(),setPointerCapture:vi.fn((id:number)=>{captured=id;}),hasPointerCapture:(id:number)=>captured===id,releasePointerCapture:vi.fn(()=>{captured=null;})};
 const event=(id:number,y:number)=>({pointerId:id,clientY:y,isPrimary:true,button:0,pointerType:'touch',currentTarget:target,preventDefault:vi.fn(),stopPropagation:vi.fn()});
 handle.onPointerDown(event(7,540));expect(target.setPointerCapture).toHaveBeenCalledWith(7);
 handle.onPointerMove(event(8,10));expect(changed).not.toHaveBeenCalled();
 handle.onPointerMove(event(7,410));expect(changed).toHaveBeenLastCalledWith(490);
 handle.onPointerUp(event(8,410));
 handle.onPointerMove(event(7,400));expect(changed).toHaveBeenLastCalledWith(500);
 handle.onPointerCancel(event(7,400));expect(target.releasePointerCapture).toHaveBeenCalledTimes(1);
 changed.mockClear();handle.onPointerMove(event(7,300));expect(changed).not.toHaveBeenCalled();
 const key={key:'ArrowUp',shiftKey:false,preventDefault:vi.fn(),stopPropagation:vi.fn()};handle.onKeyDown(key);
 expect(changed).toHaveBeenLastCalledWith(380);expect(key.preventDefault).toHaveBeenCalled();
 for(const action of [seek,play,stage,toggle])expect(action).not.toHaveBeenCalled();
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
