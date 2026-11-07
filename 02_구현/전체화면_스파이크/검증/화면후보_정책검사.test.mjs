import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import test from 'node:test';
import {readOnlyRequests, assertAllowedPort} from './화면후보_미리보기.mjs';
import {request} from '../화면/src/통신.ts';

const MESSAGE = '후보 화면 미리보기에서는 새 연구나 변경 요청을 보내지 않습니다.';

async function withServer(forwarded, run) {
  const server = createServer((incoming, response) => {
    readOnlyRequests(incoming, response, () => {
      forwarded.push(`${incoming.method} ${incoming.url}`);
      response.writeHead(200, {'Content-Type': 'application/json'});
      response.end(JSON.stringify({fixture: 'read-only-upstream'}));
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    await run(`http://127.0.0.1:${server.address().port}`);
  } finally {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
}

test('GET/HEAD는 통과시키고 POST/PUT/PATCH/DELETE는 후속 처리 전에 405로 막는다', async () => {
  const forwarded = [];
  await withServer(forwarded, async base => {
    const get = await fetch(base + '/api/spike/snapshot');
    assert.equal(get.status, 200);
    assert.deepEqual(await get.json(), {fixture: 'read-only-upstream'});
    const head = await fetch(base + '/', {method: 'HEAD'});
    assert.equal(head.status, 200);
    const before = [...forwarded];
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
      const response = await fetch(base + '/api/spike/not-a-real-action', {method});
      assert.equal(response.status, 405, `${method}: 변경 요청은 405여야 함`);
      assert.equal(response.headers.get('Allow'), 'GET, HEAD');
      assert.equal(response.headers.get('X-Hermes-Preview'), 'candidate-read-only');
      const body = await response.json();
      assert.deepEqual(body, {error: {code: 'read_only_preview', message: MESSAGE}});
      assert.deepEqual(forwarded, before, `${method}: 후속 처리 횟수 불변`);
    }
    assert.deepEqual(forwarded, ['GET /api/spike/snapshot', 'HEAD /']);
  });
});

test('실제 앱의 통신.ts가 405 본문을 undefined 없이 한국어 이유로 보여 준다', async () => {
  const forwarded = [];
  await withServer(forwarded, async base => {
    await assert.rejects(
      request(base + '/api/spike/not-a-real-action', {text: '검사용 변경 요청'}),
      error => {
        assert.equal(error.message, `read_only_preview: ${MESSAGE}`);
        assert.ok(!error.message.includes('undefined'));
        return true;
      },
    );
    assert.deepEqual(forwarded, []);
  });
});

test('기존 실행본(8766·8768)과 마을 후보(8790) 포트는 거절하고 빈 포트는 허용한다', () => {
  for (const port of [8766, 8768, 8790]) assert.throws(() => assertAllowedPort(port), /포트/);
  for (const port of [0, 80, 70000, 1.5, NaN]) assert.throws(() => assertAllowedPort(port), /포트/);
  assert.equal(assertAllowedPort(8791), 8791);
  assert.equal(assertAllowedPort(8797), 8797);
});
