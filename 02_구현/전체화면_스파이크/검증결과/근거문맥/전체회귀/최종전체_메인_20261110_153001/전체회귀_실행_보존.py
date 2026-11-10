"""Approved offline regression harness; no source edits, no OS sandbox claim."""
import os
import sys
import json
import hashlib
import re
import time
import traceback
import subprocess
from urllib.parse import urlsplit, unquote
from pathlib import Path
from collections import Counter
import xml.etree.ElementTree as ET

P = Path('C:/my vault/ME/1. Note/Hermes/Aiffel_Work/02_노드실습/딥리서치_에이전트')
I = P / '02_구현/전체화면_스파이크'
RUN = '최종전체_메인_20261110_153001'
C = P / '.cache/보고서품질_검증/실모델점검/근거문맥/최종전체' / RUN
E = I / '검증결과/근거문맥/전체회귀' / RUN
if len(sys.argv) > 1:
    assert sys.argv[1] in {'재실행_01', '재실행_02'}
    C = C / sys.argv[1]
    E = E / sys.argv[1]
E.mkdir(parents=True, exist_ok=False)
sys.stdout = (E/'전체.log').open('w', encoding='utf-8', buffering=1)
sys.stderr = sys.stdout
(C / 'temp').mkdir()

def save(name, obj):
    (E / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

spec = json.loads((I / '검증결과/근거문맥/최종검증_대상.json').read_text(encoding='utf-8'))
expected = spec['expected_pytest_modules'] + [spec['extra_main_pytest_module']]
actual = sorted(str(x.relative_to(I)).replace('\\', '/') for x in (I / '테스트').glob('테스트_*.py'))
if set(actual) != set(spec['expected_pytest_modules']) or len(actual) != 39:
    raise RuntimeError('expected module filesystem mismatch')
paths = sorted(set([x for part in ['서버', '테스트', '검증'] for x in (I / part).rglob('*.py')] + [P / s for s in spec['frontend_sources']] + [I / 'pyproject.toml']))
def hashes():
    return {str(f.relative_to(P)).replace('\\', '/'): hashlib.sha256(f.read_bytes()).hexdigest() for f in paths}
before = hashes()
save('소스해시_전.json', before)
save('기대범위.json', {'expected_modules': expected, 'filesystem_test_modules': actual, 'count_existing': len(actual), 'extra_count': 1})
save('부작용_사전감사.json', {
    'test_subprocess_hits': [{'file': str(f.relative_to(I)), 'line': n+1, 'text': l} for f in (I/'테스트').glob('*.py') for n,l in enumerate(f.read_text(encoding='utf-8').splitlines()) if re.search(r'subprocess|Popen|create_subprocess|runpy|spec_from_file_location', l)],
    'reviewed_local_children': ['Playwright PipeTransport -> project .venv playwright/driver/node.exe package/cli.js run-driver', 'Node Chromium launch -> existing project .cache/playwright/chromium-1248/chrome-win64/chrome.exe only; no existing user browser profile', 'server browser_pdf -> offline=True, javascript disabled, service_workers block, route abort; test presentation -> explicit memory route fulfillment'],
    'reviewed_helpers': ['테스트_기본경로: StorageBoundarySpy (no original DB open)', '테스트_연구조립: tmp_path project root and model/service injection', '테스트_실제모델: httpx.MockTransport with fixture env/budget', '테스트_실제연구제어: EVIDENCE redirected only, CORPUS read-only retained', '테스트_웹연구/자료모드제어: test module ROOT redirected to owned cache; captured CORPUS retained', '테스트_보고서품질_실모델점검 and extra verifier: ModelDouble injection; no main CLI execution'],
    'limitations': ['Python audit hook and Playwright launch validation are in-process guards, not an OS sandbox.', 'Node/Chromium do not inherit Python audit hook. Exact project binaries, synthetic content, isolated temporary browser profile, denied DNS/proxy and inspected explicit routes bound their approved local rendering path.']
})
# Process-local environment: preserve OS runtime variables, discard ambient credentials/config overrides.
keep = {'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','SYSTEMDRIVE','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','LOCALAPPDATA','APPDATA','USERPROFILE','HOMEDRIVE','HOMEPATH','PROGRAMFILES','PROGRAMFILES(X86)','COMMONPROGRAMFILES'}
for key in list(os.environ):
    if key.upper() not in keep:
        del os.environ[key]
os.environ.update({'TMP': str(C/'temp'), 'TEMP': str(C/'temp'), 'TMPDIR': str(C/'temp'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false', 'PYTHONDONTWRITEBYTECODE': '1', 'SPIKE_DATA_DIR': str(C/'app-default'), 'PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD': '1', 'NO_PROXY': '127.0.0.1,localhost,::1'})
os.chdir(I)
sys.path.insert(0, str(I))
import tempfile
# tempfile may cache TMP from interpreter startup; force the explicitly owned location.
tempfile.tempdir = str(C/'temp')

def norm(p):
    if isinstance(p, int) or p is None:
        return None
    return os.path.normcase(os.path.abspath(os.fsdecode(p)))
cn, en, pn = norm(C), norm(E), norm(P)
original_data = norm(P/'03_실행데이터')

def inside(p, root):
    return p == root or p.startswith(root + os.sep)
def owned(p):
    return p is not None and (inside(p, cn) or inside(p, en))
blocked = []
children = []
launches = []
audit_file = (E/'경계감사.jsonl').open('a', encoding='utf-8')

def deny(event, target):
    entry = {'event': event, 'target': str(target)}
    blocked.append(entry)
    audit_file.write(json.dumps(entry, ensure_ascii=False)+'\n')
    audit_file.flush()
    raise PermissionError('regression_guard_forbidden: ' + event + ': ' + str(target))

def check_read(p, event):
    n = norm(p)
    if n is None or owned(n):
        return
    basename = os.path.basename(n).lower()
    parts = n.replace('\\', '/').lower().split('/')
    if inside(n, original_data) or basename == '.env' or basename.endswith('.env') or any(part in {'.ssh','.aws','.azure','.config','auth.json','credentials','credentials.json','login data','cookies','network'} for part in parts):
        deny(event, n)
    if 'hermes' in parts and not inside(n, pn):
        deny(event, n)

def check_write(p, event):
    n = norm(p)
    if not owned(n):
        deny(event, n)

def loopback(address):
    return isinstance(address, tuple) and str(address[0]).lower() in {'127.0.0.1', '::1', 'localhost'} and (len(address)<2 or address[1] not in {8767,8768})

node = norm(P/'.venv/Lib/site-packages/playwright/driver/node.exe')
cli = str(P/'.venv/Lib/site-packages/playwright/driver/package/cli.js').replace('\\','/').lower()
driver_command = subprocess.list2cmdline([str(P/'.venv/Lib/site-packages/playwright/driver/node.exe'), str(P/'.venv/Lib/site-packages/playwright/driver/package/cli.js'), 'run-driver']).replace('\\','/').lower()

def audit(event, args):
    if event == 'open':
        p, mode, flags = args
        check_read(p, event)
        writing = (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if writing:
            check_write(p, event)
    elif event == 'sqlite3.connect':
        if str(args[0]) != ':memory:':
            database = str(args[0])
            if database.startswith('file:'):
                uri = urlsplit(database)
                if uri.netloc:
                    deny(event, 'sqlite remote authority')
                database = unquote(uri.path)
                if re.match(r'^/[A-Za-z]:/', database):
                    database = database[1:]
            check_write(database, event)
    elif event in {'os.listdir','os.scandir'}:
        check_read(args[0],event)
    elif event in {'os.remove','os.rmdir','os.chmod','os.utime','os.truncate'}:
        check_write(args[0],event)
    elif event == 'os.mkdir':
        if not owned(norm(args[0])) and not os.path.isdir(args[0]):
            check_write(args[0],event)
    elif event in {'os.rename','os.replace','os.link','os.symlink'}:
        check_write(args[0],event)
        check_write(args[1],event)
    elif event in {'socket.connect','socket.bind','socket.sendto'}:
        address = args[-1]
        if not loopback(address):
            deny(event,address)
    elif event in {'socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'}:
        if str(args[0]).lower() not in {'127.0.0.1','::1','localhost'}:
            deny(event,args[0])
    elif event == 'subprocess.Popen':
        executable, command, cwd, env = args
        cmd = str(command).replace('\\','/').lower()
        if (executable is not None and norm(executable) != node) or cmd != driver_command:
            deny(event, {'executable': executable, 'command': command})
        if env is None or any(env.get(k) != str(C/'temp') for k in ['TMP','TEMP','TMPDIR']):
            deny(event,'child temporary directory not isolated')
        children.append({'executable':str(executable),'command':str(command),'cwd':str(cwd)})
    elif event in {'os.system','os.exec','os.posix_spawn','os.spawn'}:
        deny(event, str(args)[:200])
sys.addaudithook(audit)

# Real local-render tests retain actual Playwright/Chromium. Harden launches without mocking renderers.
from playwright._impl._browser_type import BrowserType
original_launch = BrowserType.launch
chromium = norm(P/'.cache/playwright/chromium-1248/chrome-win64/chrome.exe')
async def guarded_launch(self, *a, **kw):
    if a or norm(kw.get('executablePath')) != chromium or kw.get('channel'):
        deny('playwright.launch', {'args':str(a),'executablePath':kw.get('executablePath')})
    flags = list(kw.get('args') or [])
    for flag in ['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost','--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=<-loopback>']:
        if flag not in flags:
            flags.append(flag)
    kw['args'] = flags
    launches.append({'executable':kw['executablePath'],'args':flags,'headless':kw.get('headless')})
    return await original_launch(self,*a,**kw)
BrowserType.launch = guarded_launch

import pytest
class Coverage:
    def __init__(self):
        self.collected=[]
        self.reports=[]
        self.collection_reports=[]
        self.redirects=[]
    def pytest_collection_modifyitems(self, session, config, items):
        self.collected=[item.nodeid for item in items]
        # Only runtime storage configuration changes; no source/test code is rewritten.
        replacements = {
            '테스트_실제연구제어.py': {'EVIDENCE': C/'fixtures/local'},
            '테스트_웹연구.py': {'ROOT': C/'fixtures/web-project'},
            '테스트_자료모드제어.py': {'ROOT': C/'fixtures/source-mode-project'},
            '테스트_보고서보완연결.py': {'SCRATCH': C/'fixtures/correction'},
            '보고서품질_실모델점검.py': {'WORK': C},
        }
        for module, obj in list(sys.modules.items()):
            attrs = replacements.get(Path(getattr(obj, '__file__', '') or '').name)
            if Path(getattr(obj, '__file__', '') or '').name == '테스트_보고서품질_실모델점검.py':
                probe = obj.probe
                self.redirects.append({'module':module+'.probe','name':'WORK','before':str(probe.WORK),'after':str(C)})
                probe.WORK = C
            if attrs:
                for name,value in attrs.items():
                    self.redirects.append({'module':module,'name':name,'before':str(getattr(obj,name)),'after':str(value)})
                    setattr(obj,name,value)
        save('수집범위.json',{'nodeids':self.collected,'modules':sorted({n.split('::')[0] for n in self.collected}),'storage_redirects':self.redirects})
    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_reports.append({'nodeid':report.nodeid,'longrepr':str(report.longrepr)})
    def pytest_runtest_logreport(self, report):
        self.reports.append({'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,'duration':report.duration,'subtest':hasattr(report,'context'),'longrepr':str(report.longrepr) if report.failed else None})
    def pytest_sessionfinish(self, session, exitstatus):
        save('완료범위.json',{'exitstatus':int(exitstatus),'collected':self.collected,'reports':self.reports,'collection_errors':self.collection_reports,'storage_redirects':self.redirects})

coverage=Coverage()
args=['-p','pytest_asyncio.plugin','-p','no:cacheprovider','테스트',spec['extra_main_pytest_module'],'-q','--basetemp='+str(C/'pytest'),'--junitxml='+str(E/'전체.xml'),'--log-file='+str(E/'pytest_logging.log')]
save('실행명령.json', {'executable':sys.executable,'python_version':sys.version,'launcher_argv':sys.argv,'pytest_args':args,'equivalent_command':[sys.executable,'-I','-B','-X','utf8',str(Path(__file__).resolve()), *sys.argv[1:]],'cwd':str(I),'environment':{k:os.environ.get(k) for k in ['TMP','TEMP','TMPDIR','PYTEST_DISABLE_PLUGIN_AUTOLOAD','LANGSMITH_TRACING','LANGCHAIN_TRACING_V2','SPIKE_DATA_DIR']},'guard_scope':'in-process audit + exact Playwright subprocess/launch allowlist; not OS sandbox'})
start=time.monotonic()
exitcode=3
try:
    exitcode=int(pytest.main(args,plugins=[coverage]))
except BaseException:
    traceback.print_exc()
finally:
    after=hashes()
    save('소스해시_후.json',after)
    drift={k:{'before':before.get(k),'after':after.get(k)} for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
    cases=[]
    xml_errors=[]
    xml_path=E/'전체.xml'
    if xml_path.exists():
        root=ET.parse(xml_path).getroot()
        for tc in root.iter('testcase'):
            cases.append({'classname':tc.get('classname'),'name':tc.get('name'),'failure':[x.text for x in tc.findall('failure')],'error':[x.text for x in tc.findall('error')],'skipped':[x.text for x in tc.findall('skipped')]})
    collected_modules=sorted({n.split('::')[0] for n in coverage.collected})
    completed=[r['nodeid'] for r in coverage.reports if r['when']=='teardown' and not r['subtest']]
    completed_modules=sorted({n.split('::')[0] for n in completed})
    outcomes=Counter(r['outcome'] for r in coverage.reports if r['when']=='call' and not r['subtest'])
    subtests=Counter(r['outcome'] for r in coverage.reports if r['subtest'])
    result={'run_id':RUN,'exit_code':exitcode,'elapsed_seconds':time.monotonic()-start,'expected_modules':expected,'collected_modules':collected_modules,'completed_modules':completed_modules,'missing_collected_modules':sorted(set(expected)-set(collected_modules)),'unexpected_collected_modules':sorted(set(collected_modules)-set(expected)),'missing_completed_modules':sorted(set(expected)-set(completed_modules)),'collected_testcase_count':len(coverage.collected),'completed_testcase_count':len(completed),'missing_completed_nodeids':sorted(set(coverage.collected)-set(completed)),'call_outcomes':dict(outcomes),'subtest_outcomes':dict(subtests),'junit_testcase_element_count':len(cases),'junit_failed_testcases':[c for c in cases if c['failure']],'junit_error_testcases':[c for c in cases if c['error']],'junit_skipped_testcases':[c for c in cases if c['skipped']],'failed_reports':[r for r in coverage.reports if r['outcome']=='failed'],'collection_errors':coverage.collection_reports,'source_hash_count':len(before),'source_hash_drift':drift,'guard_denials':blocked,'approved_children':children,'real_chromium_launches':launches,'storage_redirects':coverage.redirects,'actual_provider_calls':'not performed; MockTransport/model doubles; external Python socket/DNS blocked','guard_limitations':['Not an OS sandbox; Node/Chromium audited by inspected paths and launch configuration, not OS-level network rules.'],'paths':{'evidence':str(E),'cache':str(C),'runner':str(Path(__file__).resolve()),'log':str(E/'전체.log'),'junit':str(xml_path),'coverage':str(E/'완료범위.json')}}
    result['gate_passed']=exitcode==0 and not result['missing_collected_modules'] and not result['unexpected_collected_modules'] and not result['missing_completed_modules'] and not result['missing_completed_nodeids'] and not result['failed_reports'] and not drift
    save('결과.json',result)
    audit_file.close()
    print('\nHARNESS_RESULT',json.dumps({k:result[k] for k in ['exit_code','gate_passed','collected_testcase_count','completed_testcase_count','call_outcomes','subtest_outcomes','junit_testcase_element_count','source_hash_drift']},ensure_ascii=False))
sys.exit(exitcode)
