import { it, expect, vi, afterEach } from 'vitest';
import { sendCommand, api } from './통신';
afterEach(()=>vi.unstubAllGlobals());
it('자료를 캡처한 명령은 원문과 선택을 함께 전송하고 미확인 자료는 생략한다',async()=>{
 const fetcher=vi.fn().mockImplementation(async()=>new Response('{}'));vi.stubGlobal('fetch',fetcher);
 await sendCommand('  /큐 /웹 질문  ','frozen-web','web');
 await sendCommand('/DB','unknown');
 expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({text:'  /큐 /웹 질문  ',client_request_id:'frozen-web',source_mode:'web'});
 expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({text:'/DB',client_request_id:'unknown'});
});
it('createExport는 해당 정본 해시와 형식만 POST하며 202 메타데이터와 409 사유를 보존한다',async()=>{
 const body={report_id:'B/C',source_report_hash:'exact-hash',format:'pdf',status:'generating'};
 const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(body),{status:202})).mockResolvedValueOnce(new Response(JSON.stringify({error:{code:'report_hash_mismatch',message:'정본 해시 불일치'}}),{status:409}));vi.stubGlobal('fetch',fetcher);
 const result=await api.createExport('B/C','pdf','exact-hash').catch(()=>null);
 expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/spike/reports/B%2FC/exports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format:'pdf',source_report_hash:'exact-hash'})});
 expect(result).toEqual(body);
 await expect(api.createExport('B/C','html','old-hash')).rejects.toThrow('report_hash_mismatch: 정본 해시 불일치');
 expect(fetcher).toHaveBeenCalledTimes(2);
});
it('exports는 인코딩한 보고서의 collection만 GET하고 본문을 그대로 반환한다',async()=>{
 const body={report_id:'A/B',source_report_hash:'hash-A',exports:[]};
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(body)));vi.stubGlobal('fetch',fetcher);
 const result=await api.exports('A/B').catch(()=>null);
 expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/spike/reports/A%2FB/exports',undefined);
 expect(result).toEqual(body);
});
it('archive는 실행 ID를 인코딩한 GET만 수행하며 현재 실행이나 작업을 변경하지 않는다',async()=>{
 const body={run:{run_id:'old/A'},events:[{event_id:'fixed'}],truncated:false};
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(body)));vi.stubGlobal('fetch',fetcher);
 const result=await api.archive('old/A');
 expect(result).toEqual(body);
 expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/spike/runs/old%2FA/archive',undefined);
});
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
