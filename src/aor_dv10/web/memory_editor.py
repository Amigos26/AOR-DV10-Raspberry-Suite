"""Bank/slot editor: durable backups, optimistic concurrency and verified writes."""
import asyncio
import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from ..device_types import MemoryBankInfo, MemoryChannelInfo
from ..memory import MemoryBank, MemoryChannel, parse_backup_csv, write_backup_csv
from ..protocol.codec import DV10ProtocolError
from . import viewer_upload as upload

router = APIRouter()
STATIC = Path(__file__).parent / 'static'
BACKUPS = upload.CHANNEL_PATH.parent / 'backups' / 'memory-editor'
_device = _command_lock = None
_active = None
_jobs = {}
_tasks = set()
_guard = threading.RLock()
MODES = {'00','10','20','30','40','50','60','70','80','90','F0','F1','F2','F3','F4','F5','F6'}
STEPS = {10,50,100,500,1000,2000,5000,6250,7500,8330,9000,10000,12500,15000,20000,25000,30000,50000,100000,500000}


def configure(get_device, command_lock):
    global _device, _command_lock
    _device, _command_lock = get_device, command_lock


def _identifier(value):
    if not re.fullmatch(r'[0-9a-f]{32}', value):
        raise HTTPException(404, 'Nie znaleziono kopii lub operacji.')
    return value


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.editor-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _json(path, value):
    _atomic(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


@contextmanager
def _serial(device):
    with device._chan.transaction():
        previous = device._chan.timeout
        device._chan.timeout = max(previous, 15)
        try: yield
        finally: device._chan.timeout = previous


def _channel(c):
    return {'channel': c.channel, 'frequency_hz': c.frequency_hz if c.registered else None,
            'name': c.tag.strip() if c.registered else '',
            'mode': '0' + c.mode[-2:] if c.registered and c.mode else '0F0',
            'step_hz': c.step_hz or 12500, 'step_adjust_hz': c.step_adjust_hz or 0,
            'skip': bool(c.pass_channel) if c.registered else False,
            'protect': bool(c.write_protect) if c.registered else False}


def _key(c):
    if c['frequency_hz'] is None: return (None,)
    return (c['frequency_hz'], c['name'].strip(), c['mode'][-2:], c['step_hz'],
            c['step_adjust_hz'], c['skip'], c['protect'])


def _revision(bank):
    value = [bank['bank'], bank['name'], bank['protect'], [_key(c) for c in bank['channels']]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def _read(device, bank):
    try: channels = device.read_memory_bank(bank)
    except DV10ProtocolError as exc:
        if exc.result_code != 30: raise
        channels = [MemoryChannelInfo(bank=bank, channel=i, registered=False) for i in range(50)]
    try: info = device.get_memory_bank_info(bank)
    except DV10ProtocolError as exc:
        if exc.result_code != 30: raise
        info = MemoryBankInfo(bank=bank)
    if len(channels) != 50 or {c.channel for c in channels} != set(range(50)):
        raise RuntimeError('Niepełny odczyt banku. Zapis został zablokowany.')
    result = {'bank': bank, 'name': info.tag.strip(), 'protect': bool(info.protect),
              'channels': [_channel(c) for c in sorted(channels, key=lambda c: c.channel)]}
    result['revision'] = _revision(result)
    return result


def _read_stable(device, bank):
    # DV10 bulk MA reads can be inconsistent while its native scanner runs.
    # Read in VFO A and require consecutive matching snapshots before using
    # the data as either an edit revision or a pre-write backup.
    device.enter_vfo_mode('A')
    previous = _read(device, bank)
    for _ in range(3):
        time.sleep(.1)
        current = _read(device, bank)
        if current['revision'] == previous['revision']: return current
        previous = current
    raise RuntimeError('Odczyty pamięci są niespójne. Niczego nie zapisano; spróbuj ponownie przy zatrzymanym skanowaniu.')


def _copies(bank):
    if not BACKUPS.exists(): return []
    result = []
    for path in BACKUPS.glob('*/before.json'):
        try:
            data = json.loads(path.read_text())
            if data['memory']['bank'] == bank:
                result.append({'id': path.parent.name, 'created_at': data['created_at'],
                               'kind': data['kind'], 'channels': sum(c['frequency_hz'] is not None for c in data['memory']['channels'])})
        except (OSError, ValueError, KeyError, TypeError): continue
    return sorted(result, key=lambda c: c['created_at'], reverse=True)[:30]


def _text(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[\x20-\x7e]{0,12}', value):
        raise HTTPException(400, label + ': maksymalnie 12 znaków bez polskich liter.')
    return value.strip()


def _validate(body, bank):
    if not isinstance(body, dict): raise HTTPException(400, 'Niepoprawne dane zapisu.')
    revision = body.get('revision')
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision):
        raise HTTPException(400, 'Najpierw odczytaj bank z radia.')
    target = body.get('channel')
    if target is not None and (type(target) is not int or not 0 <= target < 50):
        raise HTTPException(400, 'Niepoprawny numer kanału.')
    channels = body.get('channels')
    expected = {target} if target is not None else set(range(50))
    if not isinstance(channels, list) or len(channels) != len(expected):
        raise HTTPException(400, 'Wymagany jest jeden kanał albo kompletny bank 50 pozycji.')
    normalized = []
    for c in channels:
        if not isinstance(c, dict) or type(c.get('channel')) is not int or c['channel'] not in expected:
            raise HTTPException(400, 'Niepoprawna pozycja kanału.')
        hz = c.get('frequency_hz')
        if hz is not None and (type(hz) is not int or not 100000 <= hz <= 1300000000 or hz % 10):
            raise HTTPException(400, 'Częstotliwość: 0,1–1300 MHz, do 5 miejsc po przecinku.')
        name = _text(c.get('name', ''), 'Nazwa kanału')
        mode = c.get('mode', '0F0')
        if not isinstance(mode, str) or len(mode) != 3 or mode[0] != '0' or mode[-2:] not in MODES:
            raise HTTPException(400, 'Niepoprawny tryb odbioru.')
        step, adjust = c.get('step_hz'), c.get('step_adjust_hz', 0)
        if type(step) is not int or step not in STEPS or type(adjust) is not int or not 0 <= adjust <= 500000 or adjust % 10:
            raise HTTPException(400, 'Niepoprawny krok lub korekta strojenia.')
        if type(c.get('skip')) is not bool or type(c.get('protect')) is not bool:
            raise HTTPException(400, 'Niepoprawne oznaczenia kanału.')
        if hz is None and (name or c['skip']): raise HTTPException(400, 'Pusta pozycja nie może mieć nazwy ani oznaczenia pomijania.')
        normalized.append({**c, 'name': name, 'mode': mode})
    if {c['channel'] for c in normalized} != expected:
        raise HTTPException(400, 'Lista kanałów zawiera powtórzenia.')
    return {'bank': bank, 'revision': revision, 'channel': target,
            'name': _text(body.get('name', ''), 'Nazwa banku'), 'channels': normalized}


def _write_channel(device, bank, row):
    if row['frequency_hz'] is None:
        try: device.delete_memory_channel(bank, row['channel'])
        except DV10ProtocolError as exc:
            if exc.result_code != 30: raise
        return
    value = '%02d%02d MP%d RF%010.5f ST%06.2f SH%06.2f MD%s PT%d TT%s' % (
        bank, row['channel'], row['skip'], row['frequency_hz']/1e6, row['step_hz']/1000,
        row['step_adjust_hz']/1000, row['mode'], row['protect'], row['name'].ljust(12))
    device.raw('MX', value)


def _write_name(device, bank):
    device.raw('MW', '%02d PT%d TT%s' % (bank['bank'], bank['protect'], bank['name'].ljust(12)))


def _anchor(device, bank):
    # Both receivers can retain the last flash page in their write cache. A write
    # of unchanged data in another page flushes it. Back up and verify this bank
    # too; never choose an empty slot or invent a temporary radio channel.
    candidates = []
    try:
        _, channels = parse_backup_csv(upload.CHANNEL_PATH.read_text(encoding='utf-8-sig'))
        candidates = list(dict.fromkeys(c.bank for c in channels if c.bank != bank and not c.is_empty))
    except (OSError, ValueError): pass
    candidates += [b for b in range(40) if b != bank and b not in candidates]
    for b in candidates:
        other = _read(device, b)
        if other['protect']: continue
        row = next((c for c in other['channels'] if c['frequency_hz'] is not None and not c['protect']), None)
        if row is not None: return other, row
    raise RuntimeError('Brak drugiego niechronionego banku z kanałem do bezpiecznego potwierdzenia zapisu. Nie zmieniono pamięci.')


def _flush(device, anchor):
    other, row = anchor
    _write_channel(device, other['bank'], row)
    _write_name(device, other)
    time.sleep(.2)


def _labels(bank):
    # Merge under the same lock as browser uploads. Preserve all other banks,
    # including a manual CSV replacement made while the receiver was writing.
    with upload._write_lock:
        try:
            original = upload.CHANNEL_PATH.read_text(encoding='utf-8-sig')
            banks, channels = parse_backup_csv(original)
        except (OSError, ValueError):
            return 'Pamięć zapisana. Baza nazw wymaga pełnego odczytu pamięci radia.'
        banks = [b for b in banks if b.index != bank['bank']]
        channels = [c for c in channels if c.bank != bank['bank']]
        banks.append(MemoryBank(index=bank['bank'], protect=bank['protect'], title=bank['name']))
        for c in bank['channels']:
            channels.append(MemoryChannel(bank=bank['bank'], channel=c['channel'], protect=c['protect'],
                frequency_hz=c['frequency_hz'], step_hz=c['step_hz'], mode=c['mode'],
                pass_flag=c['skip'], name=c['name']))
        text = write_backup_csv(sorted(banks, key=lambda b:b.index), sorted(channels,key=lambda c:(c.bank,c.channel)))
        if original.startswith('DEPMICRO-BACKUP,MEM BANK,AR-DV1,'):
            text = text.replace('DEPMICRO-BACKUP,MEM BANK,AR-DV10,', 'DEPMICRO-BACKUP,MEM BANK,AR-DV1,', 1)
        raw = text.encode('utf-8-sig')
        _atomic(upload.CHANNEL_PATH, raw)
    return None


def _update(job, **values):
    with _guard:
        job.update(values)
        public = {k:v for k,v in job.items() if not k.startswith('_')}
    _json(BACKUPS / job['id'] / 'operation.json', public)


def _execute(job, plan):
    device = _device()
    with _serial(device):
        before = _read_stable(device, plan['bank'])
        if before['revision'] != plan['revision']:
            raise RuntimeError('Pamięć banku zmieniła się od odczytu. Odczytaj bank ponownie; niczego nie zapisano.')
        desired = copy.deepcopy(before)
        for row in plan['channels']: desired['channels'][row['channel']] = row
        if plan['channel'] is None: desired['name'] = plan['name']
        changed = [row for row in plan['channels'] if _key(row) != _key(before['channels'][row['channel']])]
        name_changed = desired['name'] != before['name']
        family = getattr(device, 'device_family', lambda: '')()
        if family == 'DV10' and any(c['frequency_hz'] is not None and c['mode'][-2:] in {'90','F2','F3'} for c in changed):
            raise RuntimeError('Ten tryb nie jest obsługiwany jako osobny tryb w AR-DV10. Wybierz właściwy tryb przed zapisem.')
        if (changed or name_changed) and before['protect']:
            raise RuntimeError('Bank jest chroniony przed zapisem. Niczego nie zmieniono.')
        if any(before['channels'][c['channel']]['protect'] for c in changed):
            raise RuntimeError('Wybrany kanał jest chroniony przed zapisem. Niczego nie zmieniono.')
        anchor = _anchor(device, before['bank']) if changed or name_changed else None
        record = {'version':1, 'created_at':datetime.now(timezone.utc).isoformat(),
                  'kind':'kanał' if plan['channel'] is not None else 'bank', 'memory': before,
                  'flush_anchor': anchor[0] if anchor else None}
        # A failure of either durable backup prevents *all* radio writes.
        if upload.CHANNEL_PATH.exists():
            _atomic(BACKUPS/job['id']/'channels-before.csv', upload.CHANNEL_PATH.read_bytes())
        _json(BACKUPS/job['id']/'before.json', record)
        _update(job, backup_id=job['id'], phase='Kopia banku gotowa', state='running')
        if job['_cancel'].is_set():
            _update(job, state='cancelled', phase='Przerwano przed zapisem', result=before, scan_stopped=True)
            return
        expected = copy.deepcopy(before)
        if changed or name_changed: device.enter_vfo_mode('A')
        for n, row in enumerate(plan['channels'], 1):
            if job['_cancel'].is_set(): break
            if _key(row) != _key(before['channels'][row['channel']]):
                _write_channel(device, before['bank'], row)
            expected['channels'][row['channel']] = row
            _update(job, phase='Zapis pozycji %02d-%02d' % (before['bank'], row['channel']), completed=n)
        cancelled = job['_cancel'].is_set()
        if not cancelled and plan['channel'] is None:
            expected['name'] = desired['name']
            if name_changed: _write_name(device, expected)
        _update(job, phase='Sprawdzanie zapisu odczytem z radia')
        if anchor: _flush(device, anchor)
        result = _read(device, before['bank'])
        # Retry only explicit requested rows, never rewrite an unrelated slot.
        for attempt in range(2):
            mismatch = [c for c in expected['channels'] if _key(c) != _key(result['channels'][c['channel']])]
            if not mismatch and result['name'] == expected['name'] and result['protect'] == expected['protect']: break
            allowed = {c['channel'] for c in plan['channels']}
            if any(c['channel'] not in allowed for c in mismatch): break
            for c in mismatch: _write_channel(device, before['bank'], c)
            if result['name'] != expected['name']: _write_name(device, expected)
            if anchor: _flush(device, anchor)
            result = _read(device, before['bank'])
        _json(BACKUPS/job['id']/'after.json', result)
        if _revision(result) != _revision(expected):
            raise RuntimeError('Odczyt kontrolny nie zgadza się z zapisem. Zachowano kopię; odczytaj bank i sprawdź wynik przed ponownym zapisem.')
        if anchor:
            actual_anchor = _read(device, anchor[0]['bank'])
            if actual_anchor['revision'] != anchor[0]['revision']:
                _json(BACKUPS/job['id']/'anchor-after.json', actual_anchor)
                raise RuntimeError('Kontrola banku pomocniczego wykryła różnicę. Zachowano jego kopię w archiwum operacji.')
        warning = _labels(result)
        _update(job, state='cancelled' if cancelled else 'done', phase='Przerwano po bieżącej pozycji' if cancelled else 'Zapis sprawdzony',
                warning=warning, result=result, scan_stopped=True)


async def _worker(job, plan):
    global _active
    try:
        async with _command_lock: await run_in_threadpool(_execute, job, plan)
    except Exception as exc:
        try: _update(job, state='failed', phase='Zapis nie został zakończony', error=str(exc))
        except OSError:
            with _guard: job.update(state='failed', error=str(exc))
    finally:
        with _guard:
            if _active == job['id']: _active = None


def _authorize(request):
    if request.headers.get('x-memory-editor') != '1': raise HTTPException(403, 'Użyj edytora kanałów.')
    origin = request.headers.get('origin')
    if origin:
        origin = urlsplit(origin)
        if origin.scheme != request.url.scheme or origin.netloc.lower() != request.headers.get('host','').lower():
            raise HTTPException(403, 'Zapis z innej strony jest zablokowany.')


async def _body(request):
    _authorize(request)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>65536: raise HTTPException(413, 'Za dużo danych.')
        raw.extend(chunk)
    try: return json.loads(raw)
    except (ValueError, UnicodeError): raise HTTPException(400, 'Niepoprawne dane JSON.') from None


def _start(plan):
    global _active
    with _guard:
        if _active: raise HTTPException(409, 'Trwa już zapis pamięci. Poczekaj na zakończenie operacji.')
        identity = uuid.uuid4().hex
        job = {'id':identity, 'bank':plan['bank'], 'state':'queued', 'phase':'Oczekiwanie na radio',
               'completed':0, 'total':len(plan['channels']), 'backup_id':None, '_cancel':threading.Event()}
        _jobs[identity] = job
        _active = identity
    task = asyncio.create_task(_worker(job, plan))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {'id':identity, 'bank':plan['bank']}


@router.get('/memory-editor')
async def page():
    return FileResponse(STATIC/'memory_editor.html', headers={
        'Cache-Control':'no-store',
        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'",
        'X-Content-Type-Options':'nosniff'})


@router.get('/api/editor/banks')
async def catalog():
    titles = {}
    try:
        banks, _ = parse_backup_csv(upload.CHANNEL_PATH.read_text(encoding='utf-8-sig'))
        titles = {b.index:b.title.strip() for b in banks}
    except (OSError, ValueError): pass
    return {'banks':[{'bank':b,'name':titles.get(b,'')} for b in range(40)]}


@router.post('/api/editor/bank/{bank}/read')
async def read_bank(bank:int, request:Request):
    _authorize(request)
    if not 0<=bank<40: raise HTTPException(400, 'Numer banku: 00–39.')
    def read():
        with _serial(_device()): return _read_stable(_device(), bank)
    async with _command_lock:
        try: result = await run_in_threadpool(read)
        except Exception as exc: raise HTTPException(502, str(exc)) from exc
    result['backups'] = await run_in_threadpool(_copies, bank)
    return result


@router.post('/api/editor/bank/{bank}/save')
async def save(bank:int, request:Request):
    if not 0<=bank<40: raise HTTPException(400, 'Numer banku: 00–39.')
    return _start(_validate(await _body(request), bank))


@router.post('/api/editor/bank/{bank}/restore')
async def restore(bank:int, request:Request):
    body = await _body(request)
    if not isinstance(body, dict): raise HTTPException(400, 'Niepoprawne dane kopii.')
    identity = _identifier(str(body.get('backup_id','')))
    try: data = json.loads((BACKUPS/identity/'before.json').read_text())['memory']
    except (OSError, ValueError, KeyError): raise HTTPException(404, 'Nie znaleziono kopii.') from None
    if data['bank'] != bank: raise HTTPException(400, 'Kopia pochodzi z innego banku.')
    return _start(_validate({'revision':body.get('revision'), 'name':data['name'], 'channels':data['channels']}, bank))


@router.get('/api/editor/jobs/active')
async def active():
    with _guard: return {'id':_active, 'bank':_jobs[_active]['bank'] if _active else None}


@router.get('/api/editor/jobs/{identity}')
async def job_status(identity:str):
    # Status readers can occupy worker threads while waiting for the serial
    # transaction. Progress and cancellation must remain available meanwhile.
    _identifier(identity)
    with _guard:
        if identity in _jobs: return {k:v for k,v in _jobs[identity].items() if not k.startswith('_')}
    try: value=json.loads((BACKUPS/identity/'operation.json').read_text())
    except (OSError,ValueError): raise HTTPException(404, 'Nie znaleziono operacji.') from None
    if value['state'] in ('queued','running'):
        value.update(state='failed', error='Operacja została przerwana restartem usługi. Sprawdź bank; kopia pozostaje dostępna.')
    return value


@router.post('/api/editor/jobs/{identity}/cancel')
async def cancel(identity:str, request:Request):
    _authorize(request);_identifier(identity)
    with _guard:
        if identity not in _jobs: raise HTTPException(404, 'Nie znaleziono operacji.')
        _jobs[identity]['_cancel'].set()
    return {'requested':True}


@router.get('/api/editor/backups/{identity}/file')
def download(identity:str):
    path = BACKUPS/_identifier(identity)/'before.json'
    if not path.is_file(): raise HTTPException(404, 'Nie znaleziono kopii.')
    return FileResponse(path, media_type='application/json', filename='aor-memory-backup-'+identity+'.json', headers={'Cache-Control':'no-store'})
