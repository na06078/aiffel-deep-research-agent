import {expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {PastRecordList} from './과거기록목록';
import type {RunDeletePreview,RunSnapshot} from './계약';
import type {DeleteFlow} from './실행삭제.ts';

function makeRun(overrides:Partial<RunSnapshot>&{run_id:string}):RunSnapshot{
  return {
    question:'질문',control_revision:3,status:'completed',phase:'submitted',plan:[],
    report_id:null,draft:null,quality:{passed:null,reasons:[]},parent_report_id:null,
    created_at:'2026-11-07T11:44:50.584742+00:00',updated_at:'2026-11-07T11:50:00+00:00',
    mock_requests:0,actual_model_requests:0,...overrides,
  };
}

const older=makeRun({run_id:'run-old',question:'오래된 질문',created_at:'2026-11-05T01:00:00+00:00',source_mode:'local'});
const newer=makeRun({run_id:'run-new',question:'최근 질문',created_at:'2026-11-07T11:44:50.584742+00:00',source_mode:'web',status:'failed'});
const withReport=makeRun({run_id:'run-report',question:'보고서 질문',created_at:'2026-11-06T01:00:00+00:00',report_id:'rep-1',report_title:'제출된 제목'});
const planTitleOnly=makeRun({run_id:'run-plan',question:'계획만 있는 질문',created_at:'2026-11-04T01:00:00+00:00',report_id:null,report_title:'계획 단계 제목'});

const noop=()=>{};
const render=(runs:RunSnapshot[],extra:Partial<Parameters<typeof PastRecordList>[0]>={})=>
  renderToStaticMarkup(<PastRecordList runs={runs} currentRunId={null} replayRunId={null} onDetail={noop} onReplay={noop} {...extra}/>);

function findNode(node:any,predicate:(n:any)=>boolean):any{
  if(!node||typeof node!=='object')return undefined;
  if(node.props&&predicate(node))return node;
  for(const child of [node.props?.children].flat(Infinity)){const found=findNode(child,predicate);if(found)return found;}
}

it('created_at 내림차순(최신 먼저)으로 보여 주고 목록은 record-list를 유지한다',()=>{
  const html=render([older,newer,withReport]);
  expect(html).toContain('<ol class="record-list"');
  const order=[...html.matchAll(/data-run-id="([^"]+)"/g)].map(m=>m[1]);
  expect(order).toEqual(['run-new','run-report','run-old']);
});

it('created_at이 같으면 원래 배열의 역순이다',()=>{
  const a=makeRun({run_id:'a',created_at:'2026-11-07T00:00:00Z'});
  const b=makeRun({run_id:'b',created_at:'2026-11-07T00:00:00Z'});
  const order=[...render([a,b]).matchAll(/data-testid="history-item" data-run-id="([^"]+)"/g)].map(m=>m[1]);
  expect(order).toEqual(['b','a']);
});

it('항목은 data-run-id / data-current / data-replaying 표식을 가진다',()=>{
  const html=render([older,newer],{currentRunId:'run-new',replayRunId:'run-old'});
  const items=html.split('<li ').slice(1);
  const item=(id:string)=>items.find(s=>s.includes(`data-run-id="${id}"`))!;
  expect(item('run-new')).toContain('data-testid="history-item"');
  expect(item('run-new')).toContain('data-current="true"');
  expect(item('run-new')).toContain('data-replaying="false"');
  expect(item('run-old')).toContain('data-current="false"');
  expect(item('run-old')).toContain('data-replaying="true"');
  expect(item('run-new')).toContain('현재 실행');
  expect(item('run-old')).toContain('재생 중');
  expect(item('run-old')).not.toContain('현재 실행');
});

it('질문은 h3 + title, 상태 칩은 한국어 문구·data-tone·원문 data-status, 시각은 KST time',()=>{
  const html=render([newer]);
  expect(html).toContain('<h3 class="record-question" title="최근 질문">최근 질문</h3>');
  expect(html).toContain('data-tone="bad"');
  expect(html).toContain('data-status="failed"');
  expect(html).toContain('>실패<');
  expect(html).toMatch(/<time class="record-chip" dateTime="2026-11-07T11:44:50.584742\+00:00">2026-11-07 20:44<\/time>/);
  expect(html).toContain('웹 검색');
});

it('보고서 줄은 report_id가 있을 때만 제목을 보이고, 제목만 있는 실행은 보고서 없음',()=>{
  const html=render([withReport,planTitleOnly,older]);
  expect(html).toContain('보고서: 제출된 제목');
  expect(html).not.toContain('계획 단계 제목');
  expect(html.match(/보고서 없음/g)).toHaveLength(2);
});

it('report_id는 있으나 제목이 없으면(이전 기록) 보고서 없음으로 오인시키지 않는다',()=>{
  const html=render([makeRun({run_id:'x',report_id:'rep-9'})]);
  expect(html).not.toContain('보고서 없음');
  expect(html).toContain('보고서: 제목 없음');
});

it('run_id와 버전 문구를 본문에 표시하지 않는다(속성은 허용)',()=>{
  const html=render([newer]);
  const text=html.replace(/<[^>]*>/g,'|');
  expect(text).not.toContain('run-new');
  expect(text).not.toContain('버전');
});

it('두 버튼은 기존 문구로 같은 조작 행에 있고 각 콜백에 run_id를 전달한다',()=>{
  const onDetail=vi.fn(),onReplay=vi.fn();
  const tree=PastRecordList({runs:[newer],currentRunId:null,replayRunId:null,onDetail,onReplay});
  const row=findNode(tree,n=>n.props.className==='history-actions');
  expect(row).toBeDefined();
  const buttons=[row.props.children].flat(Infinity).filter((c:any)=>c?.type==='button');
  expect(buttons.map((b:any)=>b.props.children)).toEqual(['자세히 보기','기록 재생']);
  buttons[0].props.onClick();expect(onDetail).toHaveBeenCalledWith('run-new');expect(onReplay).not.toHaveBeenCalled();
  buttons[1].props.onClick();expect(onReplay).toHaveBeenCalledWith('run-new');
  // 삭제 버튼은 별도 구성요소로 같은 행에 추가되며 앞의 두 버튼은 그대로다(삭제 확인은 열기 전에는 없다).
  expect(render([newer])).not.toContain('history-delete-confirm');
});

it('비어 있으면 안내 문구만 보인다',()=>{
  const html=render([]);
  expect(html).toContain('보존된 실행이 없습니다.');
  expect(html).not.toContain('record-list');
});

// ---- 삭제 진입점과 카드 안 확인 (SPEC 28.5) ----
const counts={events:12,calls:4,activity_spans:6,requests:3,reports:1,report_files:1,export_files:2,web_sources:5,web_reads:7};
const readyPreview:RunDeletePreview={run_id:'run-new',deletable:true,blockers:[],counts};
const readyFlow=(runId:string):DeleteFlow=>({phase:'ready',runId,requestId:'req-1',preview:readyPreview});
const items=(html:string)=>html.split('<li ').slice(1);
const itemOf=(html:string,id:string)=>items(html).find(s=>s.includes(`data-run-id="${id}"`))!;

it('모든 카드의 조작 행에 삭제 버튼이 있고 기존 두 버튼 뒤에 온다',()=>{
  const html=render([older,newer]);
  expect(html.match(/data-testid="history-delete"/g)).toHaveLength(2);
  const row=itemOf(html,'run-new').match(/<div class="history-actions">(.*?)<\/div>/)![1];
  expect(row.indexOf('자세히 보기')).toBeLessThan(row.indexOf('기록 재생'));
  expect(row.indexOf('기록 재생')).toBeLessThan(row.indexOf('history-delete'));
});

it('running 실행의 삭제 버튼만 비활성이고 title이 있다',()=>{
  const running=makeRun({run_id:'run-live',status:'running',created_at:'2026-11-08T00:00:00Z'});
  const html=render([running,newer]);
  const live=itemOf(html,'run-live').match(/<button[^>]*history-delete"[^>]*>/)![0];
  expect(live).toContain('disabled=""');
  expect(live).toContain('title="진행 중인 실행은 삭제할 수 없습니다. 먼저 취소하세요."');
  expect(itemOf(html,'run-new').match(/<button[^>]*history-delete"[^>]*>/)![0]).not.toContain('disabled');
});

it('확인 상태는 해당 카드 안에만 나타난다(한 번에 하나)',()=>{
  const html=render([older,newer],{deleteFlow:readyFlow('run-new')});
  expect(html.match(/data-testid="history-delete-confirm"/g)).toHaveLength(1);
  expect(itemOf(html,'run-new')).toContain('history-delete-confirm');
  expect(itemOf(html,'run-new')).toContain('사건 12건 · 보고서 1건 · 변환 파일 2개 · 웹 자료 5건을 영구 삭제합니다.');
  expect(itemOf(html,'run-old')).not.toContain('history-delete-confirm');
});

it('삭제 흐름이 없으면 확인 상태가 없다',()=>{
  expect(render([newer])).not.toContain('history-delete-confirm');
  expect(render([newer],{deleteFlow:{phase:'idle'}})).not.toContain('history-delete-confirm');
});

it('삭제 시작·확인·취소 콜백을 연결한다',()=>{
  const onDeleteStart=vi.fn(),onDeleteConfirm=vi.fn(),onDeleteCancel=vi.fn();
  const tree=PastRecordList({runs:[newer],currentRunId:null,replayRunId:null,onDetail:noop,onReplay:noop,deleteFlow:readyFlow('run-new'),onDeleteStart,onDeleteConfirm,onDeleteCancel});
  const deleteButton=findNode(tree,n=>n.props.onStart!==undefined);
  deleteButton.props.onStart('run-new');expect(onDeleteStart).toHaveBeenCalledWith('run-new');
  const confirm=findNode(tree,n=>n.props.onConfirm!==undefined);
  confirm.props.onConfirm();confirm.props.onCancel();
  expect(onDeleteConfirm).toHaveBeenCalledOnce();expect(onDeleteCancel).toHaveBeenCalledOnce();
});
