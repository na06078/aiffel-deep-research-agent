import type { Actor } from './마을배치';
import type { SceneAction } from './장면상태';

// 장면과 업무 UI의 연결. 업무/기록은 앱, Pixi 자원과 이동은 마을이 소유한다.
export interface ActorPresentation {
  status: string;
  eventId?: string;
  summary?: string;
}
export interface SceneClock {
  at_ms: number;
  actor_started_ms: Partial<Record<Actor, number>>;
  report_submitted_ms?: number;
  replay: boolean;
}
export interface VillageProps {
  mode: 'assets' | 'control';
  demo: { action: SceneAction; nonce: number };
  actors: Record<Actor, ActorPresentation>;
  code?: { eventId: string; actor: Actor };
  latestReport?: { id: string; title: string; createdAt: string };
  clock?: SceneClock;
  onActorClick: (actor: Actor) => void;
  onCodeClick: () => void;
  onPaperClick: () => void;
}
// Village는 같은 canvas로 전체 viewport를 채운다. mode/props 갱신은 재초기화가 아니다.
// demo는 명시적인 로컬 연출이다. control은 전달받은 실제 상태와 ID만 표시한다.
// clock은 선택한 실행 시작을0으로 하는 밀리초다. 재생 중에는 performance.now로 진행하지 않는다.
// 같은 at_ms/활동 시작 시각/props는 순서와 관계없이 같은 위치·종이 상태를 재구성한다.
// report_submitted_ms 이후2200ms 운반은 표현 규칙이지 측정한 코드/모델 소요시간이 아니다.
