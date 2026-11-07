import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';

// Vite의 proxy보다 먼저 실행한다. 후보를 둘러보다 새 연구가 시작되는 것을 막는다.
export function readOnlyRequests(request, response, next) {
  if (request.method === 'GET' || request.method === 'HEAD') {
    next();
    return;
  }
  response.writeHead(405, {
    'Content-Type': 'application/json; charset=utf-8',
    'Allow': 'GET, HEAD',
    'Cache-Control': 'no-store',
  });
  response.end(JSON.stringify({
    error: {
      code: 'read_only_preview',
      message: '마을 디자인 미리보기에서는 새 연구나 변경 요청을 보내지 않습니다.',
    },
  }));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const spike = fileURLToPath(new URL('../', import.meta.url));
  const root = resolve(spike, '../..');
  const frontend = resolve(spike, '화면');
  const assets = resolve(root, '.cache/마을재설계_후보');
  const {values} = parseArgs({options: {port: {type: 'string', default: '8790'}}});
  const port = Number(values.port);
  if (!Number.isInteger(port) || port < 1024 || port > 65535 || [8766, 8768].includes(port)) {
    throw new Error('기존 실행본과 겹치지 않는 loopback 포트를 지정해 주세요.');
  }
  if (!existsSync(resolve(assets, 'index.html'))) {
    throw new Error('프로젝트 .cache/마을재설계_후보에 화면을 먼저 빌드해 주세요.');
  }
  const {preview} = await import('../화면/node_modules/vite/dist/node/index.js');
  const server = await preview({
    configFile: false,
    root: frontend,
    build: {outDir: assets},
    plugins: [{
      name: 'village-read-only-preview',
      configurePreviewServer(instance) {
        instance.middlewares.use(readOnlyRequests);
      },
    }],
    preview: {
      host: '127.0.0.1',
      port,
      strictPort: true,
      proxy: {'/api': {target: 'http://127.0.0.1:8768', changeOrigin: false}},
      headers: {'X-Hermes-Preview': 'village-read-only'},
    },
  });
  console.log(`VILLAGE_PREVIEW_READY http://127.0.0.1:${port}/#/control READ_ONLY=true UPSTREAM=8768`);
  server.printUrls();
}
