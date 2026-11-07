import {describe,expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import type {RunDeletePreview,RunSnapshot} from './계약';
import {DeleteButton,DeleteConfirm} from './실행삭제.tsx';
import type {DeleteFlow} from './실행삭제.ts';

const counts={events:12,calls:4,activity_spans:6,requests:3,reports:1,report_files:1,export_files:2,web_sources:5,web_reads:7};
const readyPreview:RunDeletePreview={run_id:'run-A',deletable:true,blockers:[],counts};
const dependents=[1,2,3,4,5].map(n=>({run_id:`dep-${n}`,question:`후속 질문 ${n}`,created_at:'2026-11-08T00:00:00Z'}));
const blockedPreview:RunDeletePreview={
  run_id:'run-A',deletable:false,counts,
  blockers:[{code:'has_dependents',message:'이 보고서를 문맥으로 쓴 실행이 있어 삭제할 수 없습니다. 먼저 그 실행을 삭제하세요.',dependents}],
};
const base={runId:'run-A',requestId:'req-1'};
const render=(flow:DeleteFlow)=>renderToStaticMarkup(<DeleteConfirm flow={flow} onConfirm={()=>{}} onCancel={()=>{}}/>);
const run=(status:string)=>({run_id:'run-A',status} as RunSnapshot);

describe('DeleteConfirm 화면',()=>{
  it('idle·done에서는 아무것도 그리지 않는다',()=>{
    expect(render({phase:'idle'})).toBe('');
    expect(render({phase:'done',...base,result:{status:'deleted',run_id:'run-A',counts,warnings:[],current_run_id:null}})).toBe('');
  });

  it('조회 중: 그룹 + 안내 문구만, 버튼 없음',()=>{
    const html=render({phase:'loading',...base});
    expect(html).toContain('data-testid="history-delete-confirm"');
    expect(html).toContain('role="group"');
    expect(html).toContain('aria-label="삭제 확인"');
    expect(html).toContain('삭제 범위를 확인하는 중입니다.');
    expect(html).not.toContain('<button');
  });

  it('삭제 가능: 서버 개수 문구, 유지 안내, 삭제 확인(danger)·취소 버튼',()=>{
    const html=render({phase:'ready',...base,preview:readyPreview});
    expect(html).toContain('사건 12건 · 보고서 1건 · 변환 파일 2개 · 웹 자료 5건을 영구 삭제합니다.');
    expect(html).toContain('호출 예산 기록은 유지됩니다. 되돌릴 수 없습니다.');
    expect(html).toMatch(/<button[^>]*data-testid="history-delete-yes"[^>]*>삭제 확인<\/button>/);
    expect(html).toMatch(/<button[^>]*data-testid="history-delete-cancel"[^>]*>취소<\/button>/);
    expect(html).toMatch(/class="danger"[^>]*data-testid="history-delete-yes"|data-testid="history-delete-yes"[^>]*class="danger"/);
    expect(html).not.toContain('disabled');
    expect(html).not.toContain('role="alert"');
  });

  it('막힘: blockers 문구, 후속 질문 처음 3건과 외 N건, 취소만',()=>{
    const html=render({phase:'blocked',...base,preview:blockedPreview});
    expect(html).toContain('data-testid="history-delete-blocked"');
    expect(html).toContain('이 보고서를 문맥으로 쓴 실행이 있어 삭제할 수 없습니다. 먼저 그 실행을 삭제하세요.');
    expect(html).toContain('후속 질문 1');
    expect(html).toContain('후속 질문 3');
    expect(html).not.toContain('후속 질문 4');
    expect(html).toContain('외 2건');
    expect(html).toContain('data-testid="history-delete-cancel"');
    expect(html).not.toContain('history-delete-yes');
    expect(html).not.toContain('영구 삭제합니다');
  });

  it('후속 실행이 3건 이하면 외 N건을 붙이지 않는다',()=>{
    const preview={...blockedPreview,blockers:[{...blockedPreview.blockers[0],dependents:dependents.slice(0,3)}]};
    expect(render({phase:'blocked',...base,preview})).not.toContain('외 ');
  });

  it('진행 중 차단은 서버 문구를 그대로 보이고 취소만 둔다',()=>{
    const preview:RunDeletePreview={run_id:'run-A',deletable:false,counts,blockers:[{code:'run_running',message:'진행 중인 실행입니다.'}]};
    const html=render({phase:'blocked',...base,preview});
    expect(html).toContain('진행 중인 실행입니다.');
    expect(html).not.toContain('history-delete-yes');
  });

  it('삭제 중: 두 버튼 비활성과 진행 문구',()=>{
    const html=render({phase:'busy',...base,preview:readyPreview});
    expect(html).toContain('삭제하는 중입니다.');
    expect(html.match(/disabled=""/g)).toHaveLength(2);
  });

  it('오류: role=alert로 message를 보이고 삭제 확인을 다시 누를 수 있다',()=>{
    const html=render({phase:'error',...base,preview:readyPreview,message:'서버 오류 입니다'});
    expect(html).toMatch(/<p role="alert"[^>]*>서버 오류 입니다<\/p>/);
    expect(html).toContain('history-delete-yes');
    expect(html).not.toContain('disabled');
  });

  it('조회 자체가 실패하면 알림과 취소만 보인다',()=>{
    const html=render({phase:'error',...base,preview:null,message:'조회 실패'});
    expect(html).toContain('role="alert"');
    expect(html).not.toContain('history-delete-yes');
    expect(html).toContain('history-delete-cancel');
  });

  it('버튼 콜백을 연결한다',()=>{
    const onConfirm=vi.fn(),onCancel=vi.fn();
    const tree:any=DeleteConfirm({flow:{phase:'ready',...base,preview:readyPreview},onConfirm,onCancel});
    const buttons:any[]=[];
    (function walk(n:any){if(!n||typeof n!=='object')return;if(n.type==='button')buttons.push(n);[n.props?.children].flat(Infinity).forEach(walk);})(tree);
    buttons.find(b=>b.props['data-testid']==='history-delete-yes').props.onClick();
    buttons.find(b=>b.props['data-testid']==='history-delete-cancel').props.onClick();
    expect(onConfirm).toHaveBeenCalledOnce();expect(onCancel).toHaveBeenCalledOnce();
  });
});

describe('DeleteButton',()=>{
  it('평소에는 삭제 버튼(danger, history-delete)이고 title이 없다',()=>{
    const html=renderToStaticMarkup(<DeleteButton run={run('completed')} onStart={()=>{}}/>);
    expect(html).toMatch(/<button[^>]*data-testid="history-delete"[^>]*>삭제<\/button>/);
    expect(html).toContain('class="danger"');
    expect(html).not.toContain('disabled');
    expect(html).not.toContain('title=');
  });

  it('running이면 비활성이고 지정한 title을 가진다',()=>{
    const html=renderToStaticMarkup(<DeleteButton run={run('running')} onStart={()=>{}}/>);
    expect(html).toContain('disabled=""');
    expect(html).toContain('title="진행 중인 실행은 삭제할 수 없습니다. 먼저 취소하세요."');
  });

  it('클릭하면 run_id를 넘긴다',()=>{
    const onStart=vi.fn();
    (DeleteButton({run:run('completed'),onStart}) as any).props.onClick();
    expect(onStart).toHaveBeenCalledWith('run-A');
  });
});
