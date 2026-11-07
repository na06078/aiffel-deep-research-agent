import type {RunSnapshot} from './계약';
// 실패한 실행의 사유를 실행/큐 화면에 직접 보인다. 사유 필드가 생기기 전의 기록에는 없다.
export function RunFailureNotice({run}:{run:Pick<RunSnapshot,'status'|'failure'>}){
  if(run.status!=='failed')return null;
  const {failure}=run;
  return <p role="alert" data-testid="run-failure">{failure?`실행 실패 사유 · ${failure.actor} · ${failure.code}: ${failure.message}`:'실패 사유가 기록되지 않은 실행입니다. 사건 기록의 오류 항목에서 확인해 주세요.'}</p>;
}
