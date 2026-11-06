import type { Admission, Snapshot, EventDetail, Report, ReportExport, ReportExports, ExportFormat, ReleaseOutcome } from './계약';
export async function request<T>(path:string,body?:unknown):Promise<T> {
 const response=await fetch(path, body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!response.ok){const data=await response.json().catch(()=>null);throw new Error(data?.error?`${data.error.code}: ${data.error.message}`:`HTTP ${response.status}`);}
 return response.json();
}
export async function sendCommand(text:string,id:string):Promise<Admission> { return request<Admission>('/api/spike/commands',{text,client_request_id:id}); }
import type {RunArchive} from './시간계약';
export const api={
 exports:(id:string)=>request<ReportExports>(`/api/spike/reports/${encodeURIComponent(id)}/exports`),
 createExport:(id:string,format:ExportFormat,hash:string)=>request<ReportExport>(`/api/spike/reports/${encodeURIComponent(id)}/exports`,{format,source_report_hash:hash}),
 archive:(id:string)=>request<RunArchive>(`/api/spike/runs/${encodeURIComponent(id)}/archive`),
 snapshot:()=>request<Snapshot>('/api/spike/snapshot'),
 detail:(id:string)=>request<EventDetail>(`/api/spike/events/${encodeURIComponent(id)}`),
 report:(id:string)=>request<Report>(`/api/spike/reports/${encodeURIComponent(id)}`),
 release:(ids:string[],outcome:ReleaseOutcome)=>request<{released_gate_ids:string[]}>('/api/spike/releases',{gate_ids:ids,outcome}),
 cancel:(run_id:string,control_revision:number)=>request('/api/spike/cancel',{run_id,control_revision,client_request_id:crypto.randomUUID()}),
 continue:()=>request('/api/spike/queue/continue',{client_request_id:crypto.randomUUID()}),
};
