import {expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {createPresentationSession,PresentationContent} from './보고서표현';
import type {PresentationClient} from './보고서표현';
import {REPORT_RENDERER_VERSION} from './계약';
import type {ReportPresentation} from './계약';
const documentHTML='<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'"></head><body><table><tr><th>날짜</th></tr><tr><td>2026년</td></tr></table><svg viewBox="0 0 100 40"><title>흐름</title><text x="0" y="20">근거</text></svg></body></html>';
const value=(extra:Partial<ReportPresentation>={}):ReportPresentation=>({report_id:'A',source_report_hash:'hash-A',renderer_version:REPORT_RENDERER_VERSION,html:documentHTML,warnings:[],links:[],...extra});
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const deferred=<T,>()=>{let resolve!:(value:T)=>void;let reject!:(error:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const client=()=>({presentation:vi.fn<PresentationClient['presentation']>().mockResolvedValue(value())});
it.each([
 ['report_id','B','report_id_mismatch'],
 ['source_report_hash','hash-new','report_hash_mismatch'],
 ['renderer_version','old-renderer','renderer_version_mismatch'],
] as const)('%s 불일치는 HTML을 표시하지 않는다',async(field,bad,code)=>{
 const api=client();api.presentation.mockResolvedValue(value({[field]:bad}));
 const s=createPresentationSession('A','hash-A',api);s.start();await flush();
 expect(s.getState().error).toContain(code);expect(s.getState().presentation).toBeNull();s.stop();
});
it('닫기·삭제·전환 후 늦은 성공과 실패는 상태나 구독을 되살리지 않는다',async()=>{
 for(const rejected of [false,true]){
  const api=client(),late=deferred<ReportPresentation>();api.presentation.mockReturnValueOnce(late.promise);
  const a=createPresentationSession('A','hash-A',api),listener=vi.fn();const unsubscribe=a.subscribe(listener);a.start();a.stop();unsubscribe();const stopped=a.getState();listener.mockClear();
  const bApi=client();bApi.presentation.mockResolvedValue(value({report_id:'B',source_report_hash:'hash-B'}));const b=createPresentationSession('B','hash-B',bApi);b.start();await flush();
  if(rejected)late.reject(new Error('report_not_found: 삭제됨'));else late.resolve(value());await flush();
  expect(a.getState()).toBe(stopped);expect(listener).not.toHaveBeenCalled();expect(b.getState().presentation?.report_id).toBe('B');b.stop();
 }
});
it('재시작은 이전 요청 세대를 버리며 중복 start와 조회를 합친다',async()=>{
 const api=client(),old=deferred<ReportPresentation>();api.presentation.mockReturnValueOnce(old.promise).mockResolvedValueOnce(value({html:'새 세대'}));
 const s=createPresentationSession('A','hash-A',api);s.start();s.start();void s.refresh();expect(api.presentation).toHaveBeenCalledTimes(1);
 s.stop();s.start();await flush();old.resolve(value({html:'이전 세대'}));await flush();expect(s.getState().presentation?.html).toBe('새 세대');expect(api.presentation).toHaveBeenCalledTimes(2);s.stop();
});
it('실패는 자동 재시도 없이 사유를 보존하고 명시 재조회로만 복구한다',async()=>{
 const api=client();api.presentation.mockRejectedValueOnce(new Error('HTTP 503'));
 const s=createPresentationSession('A','hash-A',api);await s.refresh();expect(api.presentation).not.toHaveBeenCalled();
 s.start();await flush();expect(s.getState()).toEqual({loading:false,error:'HTTP 503',presentation:null});await flush();expect(api.presentation).toHaveBeenCalledTimes(1);
 await s.refresh();expect(s.getState().error).toBe('');expect(s.getState().presentation).toEqual(value());s.stop();await s.refresh();expect(api.presentation).toHaveBeenCalledTimes(2);
});
it('전체 문서를 빈 sandbox srcDoc으로 격리하고 출처와 표현 경고를 밖에서 읽는다',()=>{
 const html=renderToStaticMarkup(<PresentationContent state={{loading:false,error:'',presentation:value({warnings:['<script>거절된 도식</script>'],links:[{text:'<b>원문</b>',url:'https://example.org/a?x=1&y=2'}]})}} onRetry={()=>{}}/>);
 expect(html).toContain('<iframe');expect(html).toContain('sandbox=""');expect(html).toContain('title="보고서 본문 · 표와 도식"');expect(html).toContain('srcDoc="&lt;!doctype html&gt;');expect(html).toContain('&lt;table&gt;');expect(html).toContain('&lt;svg');
 expect(html).not.toContain('allow-scripts');expect(html).not.toContain('allow-same-origin');expect(html).not.toContain('<table>');expect(html).not.toContain('<script>');
 expect(html).toContain('표현 시 주의');expect(html).toContain('출처 링크');expect(html).toContain('target="_blank" rel="noopener noreferrer"');expect(html).toContain('&lt;b&gt;원문&lt;/b&gt;');expect(html).not.toMatch(/<details[^>]*open/);
});
it('조회 로딩과 실패 원인·명시 재조회는 원문 안내와 함께 읽을 수 있다',()=>{
 const render=(loading:boolean,error:string)=>renderToStaticMarkup(<PresentationContent state={{loading,error,presentation:null}} onRetry={()=>{}}/>);
 expect(render(true,'')).toContain('role="status"');expect(render(true,'')).toContain('보고서 본문을 불러오는 중');
 const html=render(false,'HTTP 503 <script>');expect(html).toContain('role="alert"');expect(html).toContain('HTTP 503 &lt;script&gt;');expect(html).toContain('본문 다시 조회');expect(html).toContain('Markdown 원문');expect(html).not.toContain('<iframe');
});
it('서버 응답도 http(s) 절대 URL만 외부 링크로 제공한다',()=>{
 const unsafe=['javascript:alert(1)','data:text/html,bad','file:///C:/secret','//evil.example','/relative','https://','https://user:pass@example.org',42,null,{}];
 const html=renderToStaticMarkup(<PresentationContent state={{loading:false,error:'',presentation:value({links:[{text:'안전 https',url:'https://example.org'},{text:'안전 http',url:'http://example.org'},...unsafe.map((url,i)=>({text:`거절-${i}`,url:url as string}))]})}} onRetry={()=>{}}/>);
 expect(html).toContain('안전 https');expect(html).toContain('안전 http');expect(html).not.toContain('거절-');expect(html).toContain('출처 링크 (2)');
});
it('선택 ID와 해시의 표현만 조회하고 표와 SVG가 있는 전체 HTML을 보존한다',async()=>{
 const api=client(),s=createPresentationSession('A','hash-A',api),listener=vi.fn();const unsubscribe=s.subscribe(listener);
 expect(api.presentation).not.toHaveBeenCalled();s.start();await flush();expect(listener).toHaveBeenCalledTimes(2);unsubscribe();
 expect(api.presentation).toHaveBeenCalledExactlyOnceWith('A','hash-A');
 expect(s.getState()).toEqual({loading:false,error:'',presentation:value()});s.stop();
});
