import type {RunSnapshot} from './계약';
// 실패한 실행의 사유를 실행/큐 화면에 직접 보인다. 사유 필드가 생기기 전의 기록에는 없다.
export function RunFailureNotice({run}:{run:Pick<RunSnapshot,'status'|'failure'>&Partial<Pick<RunSnapshot,'quality'>>}){
  const reasons=run.quality?.reasons??[];
  if(run.status==='blocked'&&(reasons.includes('semantic_review_failed')||reasons.includes('semantic_review_invalid'))){
    const invalid=reasons.includes('semantic_review_invalid');
    const problems=run.quality?.semantic_review?.문제??[];
    return <section className="notice" role="alert" data-testid="run-quality-blocked" style={{overflowWrap:'anywhere'}}>
      <p>{invalid?'검토 응답을 확인할 수 없어 최종 제출을 차단했습니다. 내용 오류가 확정된 것은 아닙니다.':'모델의 근거 검토에서 보완이 필요하다고 판정해 최종 제출을 차단했습니다.'}</p>
      {!!problems.length&&<ul>{problems.map((problem,index)=><li key={index}>
        <strong>{problem.유형}</strong>: {problem.설명}
        <blockquote>{problem.본문발췌}</blockquote>
        <small>대조 출처: {problem.출처.join(', ')||'읽은 자료에서 확인되지 않음'}</small>
      </li>)}</ul>}
      <p>초안은 보존되며 자동 재시도하지 않습니다. 과거 기록의 상세에서 초안·검토 결과를 확인하고 조사 지시를 보완해 새 연구를 요청할 수 있습니다. 새 연구에는 모델 호출이 다시 발생합니다.</p>
    </section>;
  }
  if(run.status!=='failed')return null;
  const {failure}=run;
  return <p role="alert" data-testid="run-failure">{failure?`실행 실패 사유 · ${failure.actor} · ${failure.code}: ${failure.message}`:'실패 사유가 기록되지 않은 실행입니다. 사건 기록의 오류 항목에서 확인해 주세요.'}</p>;
}
