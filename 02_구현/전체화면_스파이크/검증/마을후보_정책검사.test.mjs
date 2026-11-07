import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import test from 'node:test';
import {readOnlyRequests} from './마을후보_미리보기.mjs';
import {request} from '../화면/src/통신.ts';

// 실제 HTTP를 통과시키되 연구 앱이나 외부 제공자에는 연결하지 않는다.
test('후보 서버는 GET/HEAD 조회를 허용하고 변경 요청은 후속 처리 전에 막는다', async () => {
  const forwarded = [];
  const server = createServer((request, response) => {
    readOnlyRequests(request, response, () => {
      forwarded.push(`${request.method} ${request.url}`);
      response.writeHead(200, {'Content-Type': 'application/json'});
      response.end(JSON.stringify({fixture: 'read-only-upstream'}));
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const get = await fetch(base + '/api/spike/snapshot');
    assert.equal(get.status, 200);
    assert.deepEqual(await get.json(), {fixture: 'read-only-upstream'});
    const head = await fetch(base + '/', {method: 'HEAD'});
    assert.equal(head.status, 200);
    const before = [...forwarded];
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
      const response = await fetch(base + '/api/spike/not-a-real-action', {method});
      assert.equal(response.status, 405, `${method}: 변경 요청은405여야 함`);
      assert.equal(response.headers.get('Allow'), 'GET, HEAD');
      assert.equal((await response.json()).error.code, 'read_only_preview');
      assert.deepEqual(forwarded, before, `${method}: 후속 처리 횟수 불변`);
    }
    assert.deepEqual(forwarded, ['GET /api/spike/snapshot', 'HEAD /']);
  } finally {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});

test('실제 앱의 오류 소비자가 후보 차단 코드와 한국어 이유를 읽는다', async () => {
  let forwarded = 0;
  const server = createServer((incoming, response) => {
    readOnlyRequests(incoming, response, () => {
      forwarded += 1;
      response.writeHead(200, {'Content-Type': 'application/json'});
      response.end('{}');
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await assert.rejects(
      request(base + '/api/spike/not-a-real-action', {text: '검사용 변경 요청'}),
      {message: 'read_only_preview: 마을 디자인 미리보기에서는 새 연구나 변경 요청을 보내지 않습니다.'},
    );
    assert.equal(forwarded, 0);
  } finally {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
