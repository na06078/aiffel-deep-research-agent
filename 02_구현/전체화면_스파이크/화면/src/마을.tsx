import { useEffect, useRef, useState } from 'react';
import { Application, Assets, Container, Graphics, Rectangle, Sprite, Texture } from 'pixi.js';
import { actors as actorIds, names, villageLayout, type Actor, type Point } from './마을배치';
import { scenePhase, type Phase } from './장면상태';
import type { VillageProps } from './장면계약';
import './마을.css';

type Layout = ReturnType<typeof villageLayout>;
interface Frame { layout: Layout; points: Record<Actor, Point>; paper: Point; phase: Phase; paperVisible: boolean }
const characterFiles: Record<Actor, string> = {
  planner: '조정자', 'researcher-1': '조사관_1', 'researcher-2': '조사관_2',
  'researcher-3': '조사관_3', 'researcher-4': '조사관_4', editor: '종합자',
};
const statusNames: Record<string, string> = {
  idle: '대기', waiting: '응답 대기', running: '작업 중', working: '작업 중',
  completed: '완료', failed: '실패', cancelled: '취소', blocked: '품질 확인', interrupted: '중단',
};
const lerp = (a: Point, b: Point, t: number): Point => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
const ease = (t: number) => 1 - Math.pow(1 - Math.max(0, Math.min(1, t)), 3);
const houseFront = (site: Point): Point => ({ x: site.x, y: site.y + 9 });
const deskFront = (site: Point): Point => ({ x: site.x + 18, y: site.y + 13 });
const toPixels = (point: Point, scale: number) => ({ left: Math.round(point.x * scale), top: Math.round(point.y * scale) });

export function Village(props: VillageProps) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const demoStart = useRef(performance.now());
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const initial = villageLayout(window.innerWidth, window.innerHeight);
  const [frame, setFrame] = useState<Frame>({ layout: initial, points: initial.sites, paper: initial.paper, phase: 'idle', paperVisible: false });
  useEffect(() => { demoStart.current = performance.now(); }, [props.demo.nonce, props.demo.action, props.mode]);

  useEffect(() => {
    const element = host.current!;
    const app = new Application();
    let disposed = false;
    let initialized = false;
    let layout = villageLayout(element.clientWidth, element.clientHeight);
    let world: Container | undefined;
    let people = {} as Record<Actor, Sprite>;
    let paper: Graphics;
    let cursor: Graphics;
    let lastView = 0;
    let reportId: string | undefined;
    let reportStart = 0;
    let resizeFrame = 0;
    const roleStarts = new Map<Actor, { identity: string; time: number }>();
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        if (!disposed && world) redraw();
      });
    });
    let sheet: Texture;
    let characterTextures: Record<Actor, Texture>;
    const tiles = new Map<string, Texture>();

    function tile(x: number, y: number) {
      const key = `${x},${y}`;
      if (!tiles.has(key)) tiles.set(key, new Texture({ source: sheet.source, frame: new Rectangle(x * 16, y * 16, 16, 16) }));
      return tiles.get(key)!;
    }
    function place(texture: Texture, x: number, y: number, parent = world!) {
      const sprite = new Sprite(texture);
      sprite.position.set(Math.round(x), Math.round(y));
      parent.addChild(sprite);
      return sprite;
    }
    function drawTree(x: number, y: number, autumn = false) {
      const column = autumn ? 9 : 4;
      for (let row = 0; row < 2; row++) for (let part = 0; part < 2; part++) {
        place(tile(column + part, row), x + part * 16, y - 16 + row * 16);
      }
    }
    function drawHouse(actor: Actor, index: number) {
      const site = layout.sites[actor];
      const start = index % 2 ? 4 : 0;
      const building = new Container();
      // 지붕 2행과 실제 벽/출입구 2행을 함께 사용한다. 픽셀 배율은 정수로 맞춘다.
      const buildingScale = Math.min(layout.scale, 3) / layout.scale;
      building.position.set(site.x - 24 * buildingScale, site.y - 64 * buildingScale);
      building.scale.set(buildingScale);
      world!.addChild(building);
      for (let y = 0; y < 4; y++) for (let x = 0; x < 3; x++) place(tile(start + x, 4 + y), x * 16, y * 16, building);
      const pc = new Graphics()
        .rect(site.x + 14, site.y + 3, 16, 2).fill(0x77553f)
        .rect(site.x + 15, site.y + 5, 2, 9).fill(0x594432)
        .rect(site.x + 27, site.y + 5, 2, 9).fill(0x594432)
        .rect(site.x + 17, site.y - 6, 11, 9).fill(0x383b3d)
        .rect(site.x + 18, site.y - 5, 9, 6).fill(0x78b3b4)
        .rect(site.x + 21, site.y + 1, 3, 2).fill(0x383b3d)
        .rect(site.x + 16, site.y + 3, 9, 1).fill(0xd3d0ba);
      world!.addChild(pc);
      const shadow = new Graphics().ellipse(site.x, site.y + 10, 6, 2).fill({ color: 0x4c6344, alpha: .4 });
      world!.addChild(shadow);
    }
    function redraw() {
      const width = element.clientWidth, height = element.clientHeight;
      if (!width || !height) return;
      layout = villageLayout(width, height);
      app.renderer.resize(width, height);
      if (world) { app.stage.removeChild(world); world.destroy({ children: true }); }
      world = new Container();
      world.scale.set(layout.scale);
      app.stage.addChild(world);
      const columns = Math.ceil(layout.width / 16), rows = Math.ceil(layout.height / 16);
      const middleX = Math.round(layout.width / 32) * 16;
      const roadRows = new Set(Object.values(layout.sites).map(site => Math.round((site.y + 14) / 16)));
      for (let row = 0; row < rows; row++) {
        for (let column = 0; column < columns; column++) {
          const x = column * 16, y = row * 16;
          const road = Math.abs(x - middleX) < 16 || (roadRows.has(row) && column > 1 && column < columns - 2);
          const hash = (column * 73 + row * 151 + column * row * 11) % 41;
          place(road ? tile(1, 2) : tile(hash === 0 ? 2 : 0, 0), x, y);
        }
      }
      // 화면 가장자리와 작업장 사이에 작은 숲을 나누어 배치한다.
      for (let y = 10; y < layout.height - 16; y += 42) {
        drawTree(3, y, Math.floor(y / 42) % 3 === 1);
        drawTree(layout.width - 19, y + 12);
      }
      for (let x = 24; x < layout.width - 24; x += 40) {
        drawTree(x, 17, Math.floor(x / 40) % 3 === 0);
        if (layout.width > 250) drawTree(x + 8, layout.height - 12);
      }
      if (layout.width > 270) {
        const pondX = layout.width * .69, pondY = layout.height * .51;
        const pond = new Graphics().rect(pondX - 15, pondY - 7, 30, 17).fill(0x699079)
          .rect(pondX - 19, pondY - 3, 38, 10).fill(0x699079)
          .rect(pondX - 13, pondY - 5, 26, 13).fill(0x518ca1)
          .rect(pondX - 17, pondY - 1, 34, 6).fill(0x518ca1)
          .rect(pondX - 7, pondY - 1, 10, 1).fill(0x9ec6c1)
          .rect(pondX + 6, pondY + 4, 7, 1).fill(0x9ec6c1);
        world.addChild(pond);
        drawTree(pondX - 35, pondY - 9);
        drawTree(pondX + 25, pondY - 4, true);
      }
      actorIds.forEach(drawHouse);
      people = {} as Record<Actor, Sprite>;
      for (const actor of actorIds) {
        const person = new Sprite(characterTextures[actor]);
        person.anchor.set(.5, 1);
        const point = houseFront(layout.sites[actor]);
        person.position.set(point.x, point.y);
        world.addChild(person);
        people[actor] = person;
      }
      paper = new Graphics().rect(-5, -12, 10, 13).fill(0x765740)
        .rect(-4, -11, 8, 11).fill(0xfff7dc)
        .rect(-2, -8, 4, 1).fill(0x9b8a68)
        .rect(-2, -5, 4, 1).fill(0x9b8a68)
        .rect(-2, -2, 3, 1).fill(0x9b8a68);
      world.addChild(paper);
      cursor = new Graphics().rect(0, 0, 3, 1).fill(0xf8e7b4);
      world.addChild(cursor);
      app.render();
      setFrame(previous => ({ ...previous, layout,
        points: Object.fromEntries(actorIds.map(actor => [actor, houseFront(layout.sites[actor])])) as Record<Actor, Point>, paper: layout.paper }));
    }
    async function start() {
      await app.init({ width: element.clientWidth, height: element.clientHeight, antialias: false, autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2), backgroundColor: 0x94b84a, roundPixels: true });
      initialized = true;
      if (disposed) { app.destroy(true, { children: true }); return; }
      const loaded = await Promise.all([
        Assets.load<Texture>('/에셋/마을_타일.png'),
        ...actorIds.map(actor => Assets.load<Texture>(`/에셋/${characterFiles[actor]}.png`)),
      ]);
      if (disposed) return;
      sheet = loaded[0];
      characterTextures = Object.fromEntries(actorIds.map((actor, index) => [actor, loaded[index + 1]])) as Record<Actor, Texture>;
      loaded.forEach(texture => { texture.source.scaleMode = 'nearest'; });
      element.appendChild(app.canvas);
      redraw();
      observer.observe(element);
      app.ticker.add(() => {
        const now = performance.now(), state = latest.current;
        const elapsed = now - demoStart.current;
        const demoPhase = scenePhase(state.demo.action, elapsed);
        const points = {} as Record<Actor, Point>;
        for (const actor of actorIds) {
          const site = layout.sites[actor], home = houseFront(site), desk = deskFront(site);
          let point = home;
          if (state.mode === 'assets') {
            if (actor === 'researcher-1' && state.demo.action === 'move') point = lerp(home, desk, reduced ? 1 : ease(elapsed / 1500));
            if (actor === 'researcher-1' && state.demo.action === 'work') point = desk;
            if (actor === 'editor' && state.demo.action === 'deliver') point = lerp(home, layout.paper, reduced ? 1 : ease(elapsed / 2200));
          } else {
            const view = state.actors[actor];
            const identity = `${view.status}:${view.eventId || ''}`;
            if (roleStarts.get(actor)?.identity !== identity) roleStarts.set(actor, { identity, time: now });
            const working = ['waiting', 'running', 'working'].includes(view.status);
            if (working) point = lerp(home, desk, reduced ? 1 : ease((now - roleStarts.get(actor)!.time) / 1300));
          }
          points[actor] = point;
          const busy = state.mode === 'assets' ? actor === 'researcher-1' && demoPhase === 'working' : ['waiting', 'running', 'working'].includes(state.actors[actor].status);
          const bounce = busy && !reduced ? Math.round(Math.sin(now / 180) * .65) : 0;
          people[actor].position.set(Math.round(point.x), Math.round(point.y + bounce));
        }
        let paperPoint = layout.paper, paperVisible = false;
        let phase = state.mode === 'assets' ? demoPhase : actorIds.some(actor => ['waiting', 'running', 'working'].includes(state.actors[actor].status)) ? 'working' as Phase : 'idle' as Phase;
        if (state.mode === 'assets') {
          paperVisible = state.demo.action === 'deliver';
          if (demoPhase === 'delivering') paperPoint = { x: points.editor.x + 7, y: points.editor.y - 4 };
        } else if (state.latestReport) {
          if (reportId !== state.latestReport.id) {
            reportId = state.latestReport.id;
            reportStart = Date.parse(state.latestReport.createdAt) >= performance.timeOrigin ? now : now - 2300;
          }
          const progress = reduced ? 1 : ease((now - reportStart) / 2200);
          paperVisible = true;
          if (progress < 1) {
            points.editor = lerp(houseFront(layout.sites.editor), layout.paper, progress);
            people.editor.position.set(points.editor.x, points.editor.y);
            paperPoint = { x: points.editor.x + 7, y: points.editor.y - 4 };
            phase = 'delivering';
          }
        }
        paper.position.set(Math.round(paperPoint.x), Math.round(paperPoint.y));
        paper.visible = paperVisible;
        const codeActor = state.code?.actor || 'researcher-1', codeSite = layout.sites[codeActor];
        cursor.position.set(codeSite.x + 19, codeSite.y - 2);
        cursor.visible = state.mode === 'assets' ? demoPhase === 'working' && (Math.floor(now / 350) % 2 === 0) : Boolean(state.code) && Math.floor(now / 350) % 2 === 0;
        if (now - lastView > 65) {
          lastView = now;
          setFrame({ layout, points, paper: paperPoint, paperVisible, phase });
        }
      });
      app.render();
      setReady(true);
    }
    void start().catch(cause => { if (!disposed) setError(`마을 이미지를 불러오지 못했습니다. ${String(cause)}`); });
    return () => {
      disposed = true;
      observer.disconnect();
      cancelAnimationFrame(resizeFrame);
      if (initialized && app.renderer) app.destroy(true, { children: true });
    };
  }, []);

  const scale = frame.layout.scale;
  const demoActive = props.mode === 'assets' && props.demo.action !== 'reset';
  const plannerBubble = props.mode === 'assets' ? demoActive : Boolean(props.actors.planner.eventId);
  const codeActor = props.code?.actor || 'researcher-1';
  const codePoint = frame.layout.sites[codeActor];
  const viewportWidth = frame.layout.width * scale;
  const codeWidth = viewportWidth < 600 ? 135 : 155;
  const codeCenter = viewportWidth < 600
    ? (codePoint.x * scale < viewportWidth / 2 ? viewportWidth - codeWidth / 2 - 8 : codeWidth / 2 + 8)
    : Math.min(viewportWidth - codeWidth / 2 - 8, (codePoint.x + 24) * scale + 120);
  const codeVisible = props.mode === 'assets' ? ['working', 'worked', 'delivering', 'submitted'].includes(frame.phase) : Boolean(props.code);
  return <div className="village-world" data-testid="world" data-render-ready={String(ready)} data-phase={frame.phase}
    data-layout-width={frame.layout.width * scale} data-layout-height={frame.layout.height * scale}>
    <div className="village-canvas" ref={host} aria-hidden="true" />
    {error && <p className="village-error" role="alert">{error}</p>}
    {ready && actorIds.map(actor => <button key={actor} type="button" className="village-actor"
      data-testid={`npc-${actor}`} data-status={props.mode === 'control' ? props.actors[actor].status : 'art-demo'}
      aria-label={`${names[actor]} · ${props.mode === 'control' ? statusNames[props.actors[actor].status] || props.actors[actor].status : '장면 실험'}`}
      style={{ ...toPixels(frame.points[actor], scale), width: 18 * scale, height: 18 * scale }} onClick={() => props.onActorClick(actor)}>
      <span className="village-name">{names[actor]}{props.mode === 'control' && props.actors[actor].status === 'waiting' && <i className="village-wait" aria-label="응답 대기" />}</span>
    </button>)}
    {ready && plannerBubble && <button type="button" className="village-bubble village-planner-bubble" data-testid="bubble-planner"
      style={toPixels(frame.points.planner, scale)} onClick={() => props.onActorClick('planner')}>
      <span>{props.mode === 'assets' ? '각자 맡은 일을 시작해 주세요.' : props.actors.planner.summary || '현재 지시와 배정을 확인하세요.'}</span>
      <small>(클릭해서 더 자세히)</small>
    </button>}
    {ready && props.mode === 'control' && actorIds.filter(actor => actor !== 'planner' && props.actors[actor].eventId).map(actor =>
      <button key={`bubble-${actor}`} type="button" className="village-bubble village-secondary-bubble" data-testid={`bubble-${actor}`}
        style={toPixels(frame.points[actor], scale)} onClick={() => props.onActorClick(actor)}>
        <span>{props.actors[actor].summary || `${names[actor]}의 보존된 작업 사건`}</span>
        <small>(클릭해서 더 자세히)</small>
      </button>)}
    {ready && codeVisible && <button type="button" className="village-bubble village-code-bubble" data-testid="bubble-workstation"
      style={{ left: codeCenter, top: (codePoint.y + 2) * scale }} onClick={props.onCodeClick}>
      <span>...</span><small>(클릭해서 더 자세히)</small>
    </button>}
    {ready && frame.paperVisible && <button type="button" className="village-paper" data-testid="paper-document"
      style={toPixels(frame.paper, scale)} aria-label="제출 문서 열기" onClick={props.onPaperClick}>
      <span>제출 문서 · 보기</span>
    </button>}
  </div>;
}
