import { it, expect, vi, afterEach } from 'vitest';
import { sendCommand, api } from './통신';
afterEach(()=>vi.unstubAllGlobals());
it('자연어와 요청 ID를 실제 POST 경계에 전달하고 202는 received로 유지한다',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({instruction_id:'i-B',status:'received',run_id:'A',mode:'immediate',control_revision:2}),{status:202}));
 vi.stubGlobal('fetch',fetcher);
 const result=await sendCommand('/큐 B를 조사해 주세요','request-B');
 expect(fetcher).toHaveBeenCalledWith('/api/spike/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'/큐 B를 조사해 주세요',client_request_id:'request-B'})});
 expect(result.status).toBe('received');
});
it('release는 명시한 이전 gate만 전송하고 서버 거절을 숨기지 않는다',async()=>{
 const fetcher=vi.fn().mockResolvedValueOnce(new Response('{"released_gate_ids":["old-A"]}')).mockResolvedValueOnce(new Response('{"error":{"code":"revision_conflict","message":"버전 충돌"}}',{status:409}));
 vi.stubGlobal('fetch',fetcher);
 await api.release(['old-A'],'failure');
 expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({gate_ids:['old-A'],outcome:'failure'});
 await expect(api.cancel('A',2)).rejects.toThrow('revision_conflict: 버전 충돌');
 expect(fetcher).toHaveBeenCalledTimes(2);
});
