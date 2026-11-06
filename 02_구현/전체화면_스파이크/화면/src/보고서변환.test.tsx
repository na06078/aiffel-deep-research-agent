import {afterEach,expect,it,vi} from 'vitest';
import {createExportSession,ExportControls} from './보고서변환';
import {renderToStaticMarkup} from 'react-dom/server';
it('준비된 형식만 서버의 해시 결합 URL로 다운로드하고 다른 형식 실패는 독립 재시도한다',()=>{
 const html=renderToStaticMarkup(<ExportControls state={{items:{html:item('html','ready'),pdf:item('pdf','failed')},loaded:true,loading:false,pending:{},errors:{},readError:''}} onRefresh={()=>{}} onCreate={()=>{}}/>);
 expect(html).toContain('data-testid="export-html-download"');expect(html).toContain('source_report_hash=hash-A&amp;renderer_version=r1');
 expect(html).not.toContain('export-pdf-download');expect(html).toContain('PDF 다시 시도');expect(html).toContain('export_timeout');expect(html).toContain('role="alert"');
});
it('미요청과 생성 중을 구별하며 조회 실패와 생성 요청 실패의 복구를 따로 표시한다',()=>{
 const state={items:{html:item('html'),pdf:item('pdf','generating')},loaded:true,loading:false,pending:{},errors:{html:'<script>생성 실패</script>'},readError:''};
 const html=renderToStaticMarkup(<ExportControls state={state} onRefresh={()=>{}} onCreate={()=>{}}/>);
 expect(html).toContain('아직 생성하지 않았습니다');expect(html).toContain('PDF 생성 중');expect(html).toContain('HTML 생성 요청 실패');expect(html).not.toContain('<script>');expect(html).toContain('role="status"');
 const failed=renderToStaticMarkup(<ExportControls state={{...state,readError:'HTTP 503'}} onRefresh={()=>{}} onCreate={()=>{}}/>);
 expect(failed).toContain('변환 상태 조회 실패');expect(failed).toContain('다시 조회');expect(failed).not.toContain('export-html-download');
});
import type {ExportClient} from './보고서변환';
import type {ExportFormat,ExportStatus,ReportExport,ReportExports} from './계약';
const item=(format:ExportFormat,status:ExportStatus='not_requested',id='A',hash='hash-A'):ReportExport=>({report_id:id,source_report_hash:hash,format,status,renderer_version:'r1',created_at:null,updated_at:null,sha256:status==='ready'?'artifact-hash':null,download_url:status==='ready'?`/api/spike/reports/${id}/exports/${format}/download?source_report_hash=${hash}&renderer_version=r1`:null,error:status==='failed'?{code:'export_timeout',message:'변환 시간 초과'}:null});
const collection=(html:ReportExport=item('html'),pdf:ReportExport=item('pdf')):ReportExports=>({report_id:html.report_id,source_report_hash:html.source_report_hash,exports:[html,pdf]});
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const deferred=<T,>()=>{let resolve!:(value:T)=>void;let reject!:(reason:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const client=()=>({exports:vi.fn<ExportClient['exports']>().mockResolvedValue(collection()),createExport:vi.fn<ExportClient['createExport']>().mockImplementation(async(id,format,hash)=>item(format,'generating',id,hash))});
afterEach(()=>vi.useRealTimers());
it('POST 대기 중 시작된 조회도 늦게 도착하여 ready를 미요청으로 되돌리지 않는다',async()=>{
 const api=client();const s=createExportSession('A','hash-A',api);s.start();await flush();
 const post=deferred<ReportExport>(),get=deferred<ReportExports>();api.createExport.mockReturnValueOnce(post.promise);const creating=s.create('html');
 api.exports.mockReturnValueOnce(get.promise);const reading=s.refresh();post.resolve(item('html','ready'));await creating;
 get.resolve(collection());await reading;expect(s.getState().items.html?.status).toBe('ready');s.stop();
});
it('같은 보고서 ID라도 해시가 바뀌면 이전 파일을 채택하지 않는다',async()=>{
 const api=client();api.exports.mockResolvedValue(collection(item('html','ready')));const s=createExportSession('A','hash-new',api);s.start();await flush();
 expect(s.getState().readError).toContain('report_hash_mismatch');expect(s.getState().items).toEqual({});s.stop();
});
it('같은 정본이어도 POST 응답 형식이 다르면 요청 형식 다운로드를 만들지 않는다',async()=>{
 const api=client();const s=createExportSession('A','hash-A',api);s.start();await flush();api.createExport.mockResolvedValue(item('pdf','ready'));await s.create('html');
 expect(s.getState().errors.html).toContain('요청한 변환 형식');expect(s.getState().items.html?.status).toBe('not_requested');expect(s.getState().items.pdf?.status).toBe('not_requested');s.stop();
});
it('생성 중 재열기는 POST 없이 현재 서버 상태를 조회하고 기존 timer도 닫을 때 제거한다',async()=>{
 vi.useFakeTimers();const api=client();api.exports.mockResolvedValue(collection(item('html','generating'),item('pdf','failed')));const s=createExportSession('A','hash-A',api);s.start();await flush();
 expect(api.createExport).not.toHaveBeenCalled();expect(vi.getTimerCount()).toBe(1);s.stop();expect(vi.getTimerCount()).toBe(0);await vi.advanceTimersByTimeAsync(2000);expect(api.exports).toHaveBeenCalledTimes(1);
});
it('생성 전 시작한 느린 조회가 생성 완료 상태를 되돌리지 않으며 중복 조회를 합친다',async()=>{
 vi.useFakeTimers();const api=client();const s=createExportSession('A','hash-A',api);s.start();await flush();
 const old=deferred<ReportExports>();api.exports.mockReturnValueOnce(old.promise);const reading=s.refresh();void s.refresh();
 api.createExport.mockResolvedValue(item('html','ready'));await s.create('html');old.resolve(collection());await reading;
 expect(api.exports).toHaveBeenCalledTimes(2);expect(s.getState().items.html?.status).toBe('ready');s.stop();
});
it('StrictMode 재시작은 이전 수명 응답을 버리고 새 조회만 채택한다',async()=>{
 const api=client();const old=deferred<ReportExports>();api.exports.mockReturnValueOnce(old.promise).mockResolvedValueOnce(collection(item('html','ready')));
 const s=createExportSession('A','hash-A',api);s.start();s.stop();s.start();await flush();old.resolve(collection());await flush();
 expect(s.getState().items.html?.status).toBe('ready');s.stop();
});
it('조회 실패는 재조회로 복구하며 생성 실패와 구별하고 해당 형식만 재시도한다',async()=>{
 vi.useFakeTimers();const api=client();api.exports.mockRejectedValueOnce(new Error('HTTP 503'));
 const s=createExportSession('A','hash-A',api);s.start();await flush();
 expect(s.getState().readError).toBe('HTTP 503');await vi.advanceTimersByTimeAsync(2000);expect(api.exports).toHaveBeenCalledTimes(1);
 await s.refresh();expect(s.getState().readError).toBe('');
 api.createExport.mockRejectedValueOnce(new Error('export_too_large: 입력 상한 초과'));
 await s.create('pdf').catch(()=>{});
 expect(s.getState().errors.pdf).toBe('export_too_large: 입력 상한 초과');expect(s.getState().pending.pdf).toBe(false);expect(s.getState().readError).toBe('');
 await s.create('pdf');expect(api.createExport.mock.calls).toEqual([['A','pdf','hash-A'],['A','pdf','hash-A']]);expect(s.getState().errors.pdf).toBe('');s.stop();
});
it('POST 응답도 정본과 요청 형식에 결합하여 다른 다운로드를 차단한다',async()=>{
 const api=client();const s=createExportSession('A','hash-A',api);s.start();await flush();api.createExport.mockResolvedValue(item('pdf','ready','B','hash-B'));
 await s.create('html').catch(()=>{});
 expect(s.getState().errors.html).toContain('report_hash_mismatch');expect(s.getState().items.html?.download_url).toBeNull();expect(s.getState().pending.html).toBe(false);s.stop();
});
it('변환 실패 메타데이터는 원인과 함께 보존하며 실패 형식만 명시 재시도한다',async()=>{
 vi.useFakeTimers();const api=client();api.exports.mockResolvedValue(collection(item('html','ready'),item('pdf','failed')));const s=createExportSession('A','hash-A',api);s.start();await flush();
 expect(s.getState().items.pdf?.error?.code).toBe('export_timeout');await s.create('html');await s.create('pdf');
 expect(api.createExport).toHaveBeenCalledExactlyOnceWith('A','pdf','hash-A');expect(s.getState().items.html?.status).toBe('ready');s.stop();
});
it('닫기 뒤 늦은 GET과 POST는 반영하거나 polling하지 않고 새 보고서 세션도 오염시키지 않는다',async()=>{
 vi.useFakeTimers();const api=client();const late=deferred<ReportExports>();api.exports.mockReturnValueOnce(late.promise);
 const a=createExportSession('A','hash-A',api);a.start();a.stop();const before=a.getState();
 const bApi=client();bApi.exports.mockResolvedValue(collection(item('html','ready','B','hash-B'),item('pdf','not_requested','B','hash-B')));
 const b=createExportSession('B','hash-B',bApi);b.start();await flush();
 late.resolve(collection(item('html','generating')));await flush();
 expect(a.getState()).toBe(before);expect(b.getState().items.html?.report_id).toBe('B');
 const p=deferred<ReportExport>();bApi.createExport.mockReturnValueOnce(p.promise);const creating=b.create('pdf');b.stop();const stopped=b.getState();
 p.resolve(item('pdf','generating','B','hash-B'));await creating;
 expect(b.getState()).toBe(stopped);await vi.advanceTimersByTimeAsync(4000);expect(api.exports).toHaveBeenCalledTimes(1);expect(bApi.exports).toHaveBeenCalledTimes(1);
});
it.each(['report','hash','child'] as const)('조회 응답의 %s 식별 불일치는 다운로드를 노출하지 않는다',async(kind)=>{
 const api=client();const data=collection(item('html','ready'));
 if(kind==='report')data.report_id='B';else if(kind==='hash')data.source_report_hash='hash-B';else data.exports[0].report_id='B';
 api.exports.mockResolvedValue(data);const s=createExportSession('A','hash-A',api);s.start();await flush();
 expect(s.getState().readError).toContain('report_hash_mismatch');expect(s.getState().items).toEqual({});expect(api.createExport).not.toHaveBeenCalled();s.stop();
});
it('형식별 즉시 중복 생성을 막고 정확한 해시로 요청하며 생성 중에만 750ms 조회한다',async()=>{
 vi.useFakeTimers();const api=client();const pending=deferred<ReportExport>();api.createExport.mockReturnValueOnce(pending.promise);
 const s=createExportSession('A','hash-A',api);s.start();await flush();
 const first=s.create('html');void s.create('html');await s.create('pdf');
 expect(api.createExport.mock.calls).toEqual([['A','html','hash-A'],['A','pdf','hash-A']]);
 expect(s.getState().pending.html).toBe(true);expect(s.getState().items.pdf?.status).toBe('generating');
 pending.resolve(item('html','generating'));await first;
 api.exports.mockResolvedValue(collection(item('html','ready'),item('pdf','ready')));
 await vi.advanceTimersByTimeAsync(749);expect(api.exports).toHaveBeenCalledTimes(1);
 await vi.advanceTimersByTimeAsync(1);expect(api.exports).toHaveBeenCalledTimes(2);
 expect(s.getState().items.html?.download_url).toContain('source_report_hash=hash-A');
 await vi.advanceTimersByTimeAsync(3000);expect(api.exports).toHaveBeenCalledTimes(2);s.stop();
});
it('열 때 collection만 조회하고 미요청 두 형식을 보존하며 polling하지 않는다',async()=>{
 vi.useFakeTimers();const api=client();const s=createExportSession('A','hash-A',api);s.start();await flush();
 expect(api.exports).toHaveBeenCalledExactlyOnceWith('A');expect(api.createExport).not.toHaveBeenCalled();
 expect(s.getState().items.html?.status).toBe('not_requested');expect(s.getState().items.pdf?.status).toBe('not_requested');
 expect(s.getState().loaded).toBe(true);expect(s.getState().loading).toBe(false);
 await vi.advanceTimersByTimeAsync(3000);expect(api.exports).toHaveBeenCalledTimes(1);s.stop();
});
