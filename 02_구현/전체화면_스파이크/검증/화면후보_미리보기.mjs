import {existsSync} from 'node:fs';
import {relative, resolve, isAbsolute} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';

const READ_ONLY_MESSAGE = '후보 화면 미리보기에서는 새 연구나 변경 요청을 보내지 않습니다.';
const BLOCKED_PORTS = [8766, 8768, 8790];

// 운영 실행본(8766·8768)과 마을 후보(8790)를 덮지 않는 loopback 포트만 허용한다.
export function assertAllowedPort(port) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535 || BLOCKED_PORTS.includes(port)) {
    throw new Error('기존 실행본·마을 후보와 겹치지 않는 1024~65535 loopback 포트를 지정해 주세요.');
  }
  return port;
}

// Vite의 proxy보다 먼저 실행한다. 후보를 둘러보다 새 연구가 시작되는 것을 막는다.
// 본문은 운영 클라이언트(화면/src/통신.ts)가 읽는 {error:{code,message}} 모양이다.
export function readOnlyRequests(request, response, next) {
  if (request.method === 'GET' || request.method === 'HEAD') {
    next();
    return;
  }
  response.writeHead(405, {
    'Content-Type': 'application/json; charset=utf-8',
    'Allow': 'GET, HEAD',
    'Cache-Control': 'no-store',
    'X-Hermes-Preview': 'candidate-read-only',
  });
  response.end(JSON.stringify({
    error: {code: 'read_only_preview', message: READ_ONLY_MESSAGE},
  }));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const spike = fileURLToPath(new URL('../', import.meta.url));
  const root = resolve(spike, '../..');
  const frontend = resolve(spike, '화면');
  const {values} = parseArgs({options: {
    port: {type: 'string', default: '8791'},
    assets: {type: 'string', default: '.cache/화면개편_후보'},
  }});
  const port = assertAllowedPort(Number(values.port));
  const assets = resolve(root, values.assets);
  const inside = relative(root, assets);
  if (!inside || inside.startsWith('..') || isAbsolute(inside)) {
    throw new Error('--assets는 프로젝트 폴더 안의 상대 경로여야 합니다.');
  }
  if (!existsSync(resolve(assets, 'index.html'))) {
    throw new Error(`프로젝트 ${values.assets}에 화면을 먼저 빌드해 주세요.`);
  }
  const {preview} = await import('../화면/node_modules/vite/dist/node/index.js');
  const server = await preview({
    configFile: false,
    root: frontend,
    build: {outDir: assets},
    plugins: [{
      name: 'candidate-read-only-preview',
      configurePreviewServer(instance) {
        instance.middlewares.use(readOnlyRequests);
      },
    }],
    preview: {
      host: '127.0.0.1',
      port,
      strictPort: true,
      proxy: {'/api': {target: 'http://127.0.0.1:8768', changeOrigin: false}},
      headers: {'X-Hermes-Preview': 'candidate-read-only'},
    },
  });
  console.log(`CANDIDATE_PREVIEW_READY http://127.0.0.1:${port}/#/control READ_ONLY=true UPSTREAM=8768 ASSETS=${relative(root, assets).replaceAll('\\', '/')}`);
  server.printUrls();
}
