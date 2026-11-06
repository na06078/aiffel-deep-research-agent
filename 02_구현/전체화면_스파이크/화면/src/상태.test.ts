import { describe, it, expect } from 'vitest';
import { acceptSnapshot, selectionResult, admittedDraft, gateTargets } from './상태';
import type { Snapshot, EventDetail } from './계약';
// 합성 DTO: 서버나 모델 실행을 대체하는 근거가 아니다.
export const snapshot = (seq: number): Snapshot => ({protocol_version:1,mode:'spike',provider:'test-double',current_run_id:null,runs:[],queue:[],queue_paused:false,queue_pause_reason:null,waiting:[],events:[],events_has_more:false,high_watermark_seq:seq,mock_requests:0,actual_model_requests:0});
describe('스냅샷 순서와 선택 사건',()=>{
 it('현재 1건과 전체 및 이전 응답을 고유 gate ID로 구분한다',()=>{
 const gates=[{gate_id:'old-A',is_stale:true},{gate_id:'B-1',is_stale:false},{gate_id:'B-2',is_stale:false}] as Snapshot['waiting'];
 expect(gateTargets(gates,'one')).toEqual(['B-1']);
 expect(gateTargets(gates,'all')).toEqual(['B-1','B-2']);
 expect(gateTargets(gates,'stale')).toEqual(['old-A']);
 expect(gateTargets([], 'all')).toEqual([]);
 });
 it('늦게 도착한 이전 조회가 최신 상태와 선택 사건을 덮지 않는다',()=>{
 const latest=snapshot(12), old=snapshot(8);
 expect(acceptSnapshot(latest,old)).toBe(latest);
 expect(acceptSnapshot(latest,snapshot(13)).high_watermark_seq).toBe(13);
 expect(acceptSnapshot(null,old)).toBe(old);
 const selected={event_id:'event-A',input:{question:'A'},output:{answer:'A'}} as EventDetail;
 expect(selectionResult(selected,3,2,{event_id:'late-B'} as EventDetail)).toBe(selected);
 expect(selectionResult(selected,3,3,{event_id:'chosen-C'} as EventDetail)?.event_id).toBe('chosen-C');
 });
 it('접수 성공은 보낸 초안만 지우며 전송 중 작성한 새 지시는 보존한다',()=>{
 expect(admittedDraft('B','A')).toBe('B');
 expect(admittedDraft('A','A')).toBe('');
 });
});
