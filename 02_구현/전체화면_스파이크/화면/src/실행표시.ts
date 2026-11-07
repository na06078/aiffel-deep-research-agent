// 실행(run)의 상태·시각을 사람이 읽는 형태로 바꾸는 순수 함수 모음.
// 타임라인 머리글과 과거 기록 목록이 같은 문구·색 의미를 쓰도록 한 곳에 둔다(기술 명세 27.2).

const STATUS_LABELS:Record<string,string>={
  running:'진행 중',
  completed:'완료',
  blocked:'차단됨',
  failed:'실패',
  cancelled:'취소됨',
  interrupted:'중단됨',
};

export type RunTone='active'|'ok'|'warn'|'bad'|'muted';

const STATUS_TONES:Record<string,RunTone>={
  running:'active',
  completed:'ok',
  blocked:'warn',
  interrupted:'warn',
  failed:'bad',
};

// 서버가 새 상태값을 추가해도 화면이 비지 않도록, 모르는 값은 원문을 그대로 보여 준다.
export function runStatusLabel(status:string):string{
  return Object.hasOwn(STATUS_LABELS,status)?STATUS_LABELS[status]:status;
}

export function runStatusTone(status:string):RunTone{
  return Object.hasOwn(STATUS_TONES,status)?STATUS_TONES[status]:'muted';
}

export const UNKNOWN_TIME='시각 확인불가';

// 로케일 구두점(예: '오후', '.')에 기대면 환경마다 문자열이 달라지므로
// formatToParts의 숫자 조각만 꺼내 'YYYY-MM-DD HH:mm'을 직접 조립한다.
const kstParts=new Intl.DateTimeFormat('en-US',{
  timeZone:'Asia/Seoul',
  year:'numeric',month:'2-digit',day:'2-digit',
  hour:'2-digit',minute:'2-digit',
  hourCycle:'h23',
});

export function formatRunTime(iso:string):string{
  const time=new Date(iso).getTime();
  if(!iso||Number.isNaN(time))return UNKNOWN_TIME;
  const parts:Record<string,string>={};
  for(const part of kstParts.formatToParts(new Date(time)))parts[part.type]=part.value;
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
