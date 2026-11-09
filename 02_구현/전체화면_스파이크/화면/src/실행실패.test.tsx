import {expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {RunFailureNotice} from './실행실패';
// 2026-11-07 실사용: 실행/큐는 `failed`만 보이고 사유 문구는 사건 상세를 열어야 읽혔다.
const failure={code:'model_error',message:'메모가 상한을 넘었습니다.',actor:'researcher-4'};
it('모델 의미 검토의 차단 사유와 지적 문장·출처·새 조사 비용을 바로 보인다',()=>{
 const run={status:'blocked' as const,quality:{passed:false,reasons:['semantic_review_failed'],semantic_review:{통과:false,문제:[{유형:'날짜',설명:'원문의 날짜와 다릅니다.',본문발췌:'<script>1883년</script>',출처:['임오군란']}],검토요약:'날짜 보완'}}};
 const html=renderToStaticMarkup(<RunFailureNotice run={run}/>);
 expect(html).toContain('role="alert"');expect(html).toContain('모델의 근거 검토');
 expect(html).toContain('원문의 날짜와 다릅니다');expect(html).toContain('임오군란');
 expect(html).toContain('&lt;script&gt;1883년&lt;/script&gt;');expect(html).not.toContain('<script>');
 expect(html).toContain('초안은 보존');expect(html).toContain('새 연구');expect(html).toContain('모델 호출');
});
it('의미 검토 응답 불명은 내용 오류 확정과 구별해 설명한다',()=>{
 const run={status:'blocked' as const,quality:{passed:false,reasons:['semantic_review_invalid']}};
 const html=renderToStaticMarkup(<RunFailureNotice run={run}/>);
 expect(html).toContain('검토 응답을 확인할 수 없어');expect(html).toContain('내용 오류가 확정된 것은 아닙니다');
 expect(html).toContain('자동 재시도하지 않습니다');
});
it('실패한 실행은 기록된 사유(보낸 역할·코드·문구)를 경고로 보인다',()=>{
 const html=renderToStaticMarkup(<RunFailureNotice run={{status:'failed',failure}}/>);
 expect(html).toContain('role="alert"');expect(html).toContain('data-testid="run-failure"');
 expect(html).toContain('researcher-4');expect(html).toContain('model_error');expect(html).toContain('메모가 상한을 넘었습니다.');
});
it('사유가 기록되지 않은 이전 실패는 사건 기록의 오류 항목을 안내한다',()=>{
 for(const missing of [undefined,null]){
  const html=renderToStaticMarkup(<RunFailureNotice run={{status:'failed',failure:missing}}/>);
  expect(html).toContain('실패 사유가 기록되지 않은 실행입니다');expect(html).toContain('사건 기록');
 }
});
it('실패가 아닌 실행에는 아무것도 그리지 않는다',()=>{
 for(const status of ['running','completed','blocked','cancelled','interrupted'] as const){
  expect(renderToStaticMarkup(<RunFailureNotice run={{status,failure}}/>)).toBe('');
 }
});
it('사유 문구의 마크업은 이스케이프된다',()=>{
 const html=renderToStaticMarkup(<RunFailureNotice run={{status:'failed',failure:{...failure,message:'<script>x</script>'}}}/>);
 expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');
});
