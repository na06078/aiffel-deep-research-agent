import type { Actor } from './마을배치';
import type { SceneAction } from './장면상태';

// 장면과 업무 UI의 연결. 업무/기록은 앱, Pixi 자원과 이동은 마을이 소유한다.
export interface ActorPresentation {
  status: string;
  eventId?: string;
  summary?: string;
}
export interface VillageProps {
  mode: 'assets' | 'control';
  demo: { action: SceneAction; nonce: number };
  actors: Record<Actor, ActorPresentation>;
  code?: { eventId: string; actor: Actor };
  latestReport?: { id: string; title: string; createdAt: string };
  onActorClick: (actor: Actor) => void;
  onCodeClick: () => void;
  onPaperClick: () => void;
}
// Village는 같은 canvas로 전체 viewport를 채운다. mode/props 갱신은 재초기화가 아니다.
// demo는 명시적인 로컬 연출이다. control은 전달받은 실제 상태와 ID만 표시한다.
