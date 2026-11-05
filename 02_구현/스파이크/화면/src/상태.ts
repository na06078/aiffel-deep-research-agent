import type { Snapshot, Gate, EventDetail, JournalEvent, RunSnapshot } from './계약';

const kstTimeFormatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  hourCycle: 'h23',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

export function formatEventTime(timestamp: string): string {
  const instant = new Date(timestamp);
  return Number.isNaN(instant.getTime()) ? '시각 확인불가' : kstTimeFormatter.format(instant);
}

export function currentRunEvents(
  events: readonly JournalEvent[],
  current: RunSnapshot | null | undefined,
): JournalEvent[] {
  if (!current) return [];
  return events.filter(event => event.run_id === current.run_id
    && event.control_revision === current.control_revision);
}

export function acceptSnapshot(current: Snapshot | null, incoming: Snapshot): Snapshot { return current && current.high_watermark_seq > incoming.high_watermark_seq ? current : incoming; }
export function gateTargets(gates: Gate[], kind: 'one'|'all'|'stale'): string[] {
 const ids=gates.filter(g=>kind==='stale'?g.is_stale:!g.is_stale).map(g=>g.gate_id);
 return kind==='one'?ids.slice(0,1):ids;
}
export function selectionResult(current: EventDetail | null,currentToken:number,responseToken:number,incoming:EventDetail): EventDetail | null { return currentToken===responseToken?incoming:current; }
export function admittedDraft(current:string,submitted:string):string{return current===submitted?'':current;}
