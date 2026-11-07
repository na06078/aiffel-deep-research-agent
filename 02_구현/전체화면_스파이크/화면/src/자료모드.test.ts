import {expect,it,vi} from 'vitest';
import {commandSource,confirmedSource} from './자료모드';
import {retryIdentity} from './장면상태';
it.each(['/웹 질문','/큐 /웹 질문','/웹 /큐 질문','  /웹  '])('명시 접두어 %s는 저장된 데이터셋보다 우선한다',text=>{
 expect(commandSource(text,'local')).toBe('web');
});
it.each(['/디비 질문','/DB 질문','/데이터셋 /큐 질문'])('명시 데이터셋 %s는 웹 선택보다 우선한다',text=>{expect(commandSource(text,'web')).toBe('local');});
it.each(['/db 질문','/web 질문','/웹사이트 질문','질문 /DB'])('지정하지 않은 별칭/본문 %s는 자료 명령이 아니다',text=>{expect(commandSource(text,'web')).toBe('web');});
it('확인된 선택은 늦은 GET과 이전 ack로 되돌아가지 않고 큐 접수로 바뀌지 않는다',()=>{
 const choice=confirmedSource(null,{kind:'mode_change',source_mode:'web',state_seq:10} as any);
 expect(choice).toEqual({source_mode:'web',seq:10});
 expect(confirmedSource(choice,{selected_source_mode:'local',high_watermark_seq:9} as any)).toEqual(choice);
 expect(confirmedSource(choice,{kind:'mode_change',source_mode:'local',state_seq:8} as any)).toEqual(choice);
 expect(confirmedSource(choice,{kind:'research',mode:'queued',source_mode:'local',state_seq:11} as any)).toEqual(choice);
 expect(confirmedSource(choice,{selected_source_mode:'local',high_watermark_seq:12} as any)).toEqual({source_mode:'local',seq:12});
 expect(confirmedSource(null,{high_watermark_seq:0,runs:[]} as any)).toEqual({source_mode:'local',seq:0});
});
it('실패한 원문은 배경 선택 변경에도 최초 자료와 ID를 재전송한다',()=>{
 const make=vi.fn().mockReturnValueOnce('first').mockReturnValueOnce('second');
 const original=retryIdentity(null,'같은 질문',make,'web');
 expect(original).toEqual({text:'같은 질문',id:'first',source_mode:'web'});
 expect(retryIdentity(original,'같은 질문',make,'local')).toBe(original);
 expect(retryIdentity(original,'/DB 다른 질문',make,'local')).toEqual({text:'/DB 다른 질문',id:'second',source_mode:'local'});
 expect(make).toHaveBeenCalledTimes(2);
});
