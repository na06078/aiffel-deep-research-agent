import {describe,expect,it} from 'vitest';
import {formatRunTime,runStatusLabel,runStatusTone} from './실행표시';

// 상태 문구와 색 의미는 타임라인 머리글과 과거 기록 목록이 공유한다(기술 명세 27.2).
describe('runStatusLabel / runStatusTone',()=>{
  it.each([
    ['running','진행 중','active'],
    ['completed','완료','ok'],
    ['blocked','차단됨','warn'],
    ['failed','실패','bad'],
    ['cancelled','취소됨','muted'],
    ['interrupted','중단됨','warn'],
  ])('%s → %s / %s',(status,label,tone)=>{
    expect(runStatusLabel(status)).toBe(label);
    expect(runStatusTone(status)).toBe(tone);
  });

  it('알 수 없는 상태는 원문 그대로, 색은 muted',()=>{
    expect(runStatusLabel('mystery')).toBe('mystery');
    expect(runStatusTone('mystery')).toBe('muted');
  });
});

describe('formatRunTime',()=>{
  it('서버의 실제 시각 형식(마이크로초 + +00:00)을 KST로 바꾼다',()=>{
    expect(formatRunTime('2026-11-07T11:44:50.584742+00:00')).toBe('2026-11-07 20:44');
  });
  it('Z 표기와 날짜 경계를 넘는 KST 변환',()=>{
    expect(formatRunTime('2026-11-06T15:30:00Z')).toBe('2026-11-07 00:30');
    expect(formatRunTime('2026-12-31T14:59:59Z')).toBe('2026-12-31 23:59');
  });
  it('명시적 +09:00 입력은 그대로 유지한다',()=>{
    expect(formatRunTime('2026-11-07T08:05:00+09:00')).toBe('2026-11-07 08:05');
  });
  it('해석할 수 없는 값은 시각 확인불가',()=>{
    expect(formatRunTime('garbage')).toBe('시각 확인불가');
    expect(formatRunTime('')).toBe('시각 확인불가');
  });
});
