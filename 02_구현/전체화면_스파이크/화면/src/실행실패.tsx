import {useRef,useState} from 'react';
import {api} from './통신';
import type {RunSnapshot} from './계약';
// 실패한 실행의 사유를 실행/큐 화면에 직접 보인다. 사유 필드가 생기기 전의 기록에는 없다.
type NoticeRun=Pick<RunSnapshot,'status'|'failure'>&Partial<Pick<RunSnapshot,'quality'|'run_id'|'control_revision'|'source_mode'|'correction_state'|'report_id'>>;
export function RunFailureNotice({run,readOnly=true}:{run:NoticeRun;readOnly?:boolean}){
  // render/새로고침은 POST하지 않는다. 같은 대상의 HTTP 재시도는 최초 ID를 유지한다.
  const attempts=useRef(new Map<string,{id:string;pending:boolean;accepted:boolean;error:string}>());
  const [,redraw]=useState(0);
  const key=JSON.stringify([run.run_id,run.control_revision]);
  const attempt=attempts.current.get(key);
  const reasons=run.quality?.reasons??[];
  const count=run.quality?.semantic_review?.문제.length??0;
  const eligible=!readOnly&&run.status==='blocked'&&run.source_mode==='local'&&
    reasons.includes('semantic_review_failed')&&!reasons.includes('semantic_review_invalid')&&
    run.quality?.semantic_review?.통과===false&&count>=1&&count<=2&&
    typeof run.run_id==='string'&&run.run_id.trim().length>0&&
    Number.isSafeInteger(run.control_revision)&&(run.control_revision??0)>0&&
    !run.correction_state&&!run.report_id;
  const current=useRef({key,eligible});current.current={key,eligible};
  async function correct(){
    if(!eligible||!current.current.eligible||current.current.key!==key)return;
    let item=attempts.current.get(key);
    if(item?.pending||item?.accepted)return;
    if(!item){item={id:crypto.randomUUID(),pending:false,accepted:false,error:''};attempts.current.set(key,item);}
    item.pending=true;item.error='';redraw(n=>n+1);
    try{await api.correction(run.run_id!,run.control_revision!,item.id);item.accepted=true;}
    catch(error){item.error=error instanceof Error?error.message:String(error);}
    finally{item.pending=false;redraw(n=>n+1);}
  }
  const accepted=attempt?.accepted;
  const correction=run.correction_state;
  const correctionNotice=correction?<p role="status" data-testid="run-correction-state">
    {correction.status==='pending'?'검토 지적 문장 보완 중입니다.':'이미 1회 보완을 시도한 실행입니다.'}
    {` 상태: ${correction.status} · 원본 버전 ${correction.source_revision} · 모델 호출 최대 ${correction.max_model_calls}회. `}
    보완을 다시 시작하지 않습니다. 원래 초안과 현재 판정은 보존됩니다.
  </p>:null;
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
      {eligible&&!accepted&&<><p>전체 연구를 다시 시작하지 않으며, 원래 초안은 보존합니다. 모델 호출 최대 2회입니다.</p><button type="button" data-testid="run-correction" disabled={attempt?.pending??false} onClick={()=>void correct()}>검토 지적 문장만1회 보완</button></>}
      {correctionNotice}
      {attempt?.pending&&<p role="status">보완 요청을 접수하는 중입니다. 중복 요청하지 않습니다.</p>}
      {accepted&&<p role="status">보완 요청이 접수되었습니다. 완료 또는 검토 통과를 뜻하지 않습니다. 최신 실행 상태를 확인해 주세요.</p>}
      {attempt?.error&&<p role="alert">{attempt.error} · 초안과 검토 지적은 보존됩니다. 다시 누르면 같은 요청 ID로 접수 여부를 확인합니다.</p>}
      <p>초안은 보존되며 자동 재시도하지 않습니다. 과거 기록의 상세에서 초안·검토 결과를 확인하고 조사 지시를 보완해 새 연구를 요청할 수 있습니다. 새 연구에는 모델 호출이 다시 발생합니다.</p>
    </section>;
  }
  if(run.status!=='failed')return correctionNotice;
  const {failure}=run;
  return <>{correctionNotice}<p role="alert" data-testid="run-failure">{failure?`실행 실패 사유 · ${failure.actor} · ${failure.code}: ${failure.message}`:'실패 사유가 기록되지 않은 실행입니다. 사건 기록의 오류 항목에서 확인해 주세요.'}</p></>;
}
