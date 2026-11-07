import {expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {Timeline} from './실행타임라인';
import type {TimelineProps} from './실행타임라인';
import type {RunArchive} from './시간계약';
import type {RunSnapshot} from './계약';
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
 for(const id of ['timeline-subject','replay-prev','replay-play','replay-next','replay-live','replay-time','timeline-scrubber'])expect(toolbar).toContain(`data-testid="${id}"`);
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
const base:TimelineProps={open:true,runs:[],runId:null,archive:null,at:0,replay:false,playing:false,loading:false,error:'',onToggle:vi.fn(),onSeek:vi.fn(),onPlay:vi.fn(),onStage:vi.fn(),onLive:vi.fn(),onRetry:vi.fn(),onEvent:vi.fn()};
it('단계 전체와 응답 대기를 범례·이름·객체별 경과로 구분한다',()=>{
 const wait={...archive.spans[0],span_id:'wait',kind:'model_wait' as const,label:'model',duration_ms:1500};
 const html=renderToStaticMarkup(<Timeline {...base} archive={{...archive,spans:[archive.spans[0],wait]}}/>);
 expect(html).toContain('단계 전체');expect(html).toContain('응답 대기');expect(html).toContain('운반 연출');
 expect(html).toContain('data-testid="timeline-total-planner"');
 expect(html).not.toContain('<span>model</span>');
});
it('하단 손잡이와 선택·재생 제어를 보존하고 기록이 없으면 정직하게 안내한다',()=>{
 const html=renderToStaticMarkup(<Timeline {...base}/>);
 for(const id of ['timeline-toggle','timeline-drawer','timeline-subject','replay-play','replay-prev','replay-next','replay-live','replay-status','replay-time'])expect(html).toContain(`data-testid="${id}"`);
 expect(html).toContain('보존된 실행이 없습니다');
 expect(renderToStaticMarkup(<Timeline {...base} open={false}/>)).not.toContain('data-testid="timeline-drawer"');
});
it('실제 제공자의 열린 실시간 타임라인에 모의 응답이라고 표시하지 않는다',()=>{
 const html=renderToStaticMarkup(<Timeline {...base} archive={{...archive,provider:'openai'}}/>);
 expect(html).toContain('실시간');
 expect(html).not.toContain('실시간 · 모의 응답');
});

// 기술 명세 27.3: 드롭다운 대신 읽기 전용 대상 표시.
const toolbarOf=(html:string)=>html.split('data-testid="timeline-toolbar"')[1]?.split('data-testid="timeline-track-scroll"')[0]??'';
const subjectOf=(html:string)=>html.split('data-testid="timeline-subject"')[1]?.split('</div>')[0]??'';
const second:RunSnapshot={...archive.run,run_id:'B',question:'두 번째 질문',status:'failed',created_at:'2026-11-07T11:44:50.584742+00:00'};
it('툴바에 select·combobox·실행 선택 testid가 없다',()=>{
  const html=renderToStaticMarkup(<Timeline {...base} runs={[archive.run,second]} archive={archive}/>);
  const toolbar=toolbarOf(html);
  expect(toolbar).not.toContain('<select');expect(toolbar).not.toContain('combobox');
  expect(html).not.toContain('timeline-run-select');
  for(const id of ['replay-prev','replay-play','replay-next','replay-live','replay-status','replay-time','timeline-scrubber'])expect(toolbar).toContain(`data-testid="${id}"`);
});
it('실시간: archive.run을 모드 칩·질문·상태 · 시각으로 표시한다',()=>{
  const html=renderToStaticMarkup(<Timeline {...base} runs={[archive.run]} archive={archive}/>);
  const head=html.split('data-testid="timeline-subject"')[1].split('>')[0];
  expect(head).toContain('data-mode="live"');expect(head).toContain('data-run-id="A"');
  const subject=subjectOf(html);
  expect(subject).toContain('>실시간<');
  expect(subject).toContain('title="saved question"');
  expect(subject).toContain('완료 · 2026-11-06 09:00');
  expect(subject).toContain('data-status="completed"');
  expect(subject).not.toContain('>completed<');
});
it('기록 재생: runId로 runs에서 같은 실행을 찾아 기록 재생 칩과 실패 tone을 보인다',()=>{
  const html=renderToStaticMarkup(<Timeline {...base} runs={[archive.run,second]} runId="B" replay archive={archive}/>);
  const head=html.split('data-testid="timeline-subject"')[1].split('>')[0];
  expect(head).toContain('data-mode="replay"');expect(head).toContain('data-run-id="B"');
  const subject=subjectOf(html);
  expect(subject).toContain('>기록 재생<');expect(subject).toContain('두 번째 질문');
  expect(subject).toContain('data-tone="bad"');expect(subject).toContain('실패 · 2026-11-07 20:44');
  expect(subject).not.toContain('saved question');
});
it('기록 재생: runs에 없으면 archive.run으로 대체한다',()=>{
  const html=renderToStaticMarkup(<Timeline {...base} runs={[]} runId="A" replay archive={archive}/>);
  expect(subjectOf(html)).toContain('saved question');
  expect(html.split('data-testid="timeline-subject"')[1].split('>')[0]).toContain('data-run-id="A"');
});
it('대상이 아직 없으면 불러오는 중, 실행이 하나도 없으면 실행 없음',()=>{
  const loading=renderToStaticMarkup(<Timeline {...base} runs={[]} runId="A" replay loading/>);
  expect(subjectOf(loading)).toContain('불러오는 중');
  expect(loading.split('data-testid="timeline-subject"')[1].split('>')[0]).not.toContain('data-run-id');
  const none=renderToStaticMarkup(<Timeline {...base}/>);
  expect(subjectOf(none)).toContain('실행 없음');expect(subjectOf(none)).not.toContain('불러오는 중');
});
it('기록 조회에 실패하면 불러오는 중이 아니라 실패를 알린다',()=>{
  const failed=renderToStaticMarkup(<Timeline {...base} runs={[archive.run]} error="404 not_found"/>);
  expect(subjectOf(failed)).toContain('기록을 불러오지 못했습니다');
  expect(subjectOf(failed)).not.toContain('불러오는 중');
});
it('해석할 수 없는 시각은 시각 확인불가, 알 수 없는 상태는 원문으로 표시',()=>{
  const odd={...archive,run:{...archive.run,status:'weird' as any,created_at:'garbage'}};
  const subject=subjectOf(renderToStaticMarkup(<Timeline {...base} runs={[odd.run]} archive={odd}/>));
  expect(subject).toContain('weird · 시각 확인불가');
});
