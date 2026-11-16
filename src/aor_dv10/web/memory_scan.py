"""Native memory-bank scanning controls for the full-control web app."""
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..memory import parse_backup_csv
from ..protocol.codec import DV10Error
from .viewer_upload import CHANNEL_PATH

router = APIRouter()
_get_device = None
_command_lock = None
_selected_banks = []
_running = False


def configure(get_device, command_lock):
    global _get_device, _command_lock
    _get_device = get_device
    _command_lock = command_lock


def bank_catalog():
    try:
        raw = CHANNEL_PATH.read_bytes()
    except FileNotFoundError:
        raise HTTPException(409, 'Najpierw pobierz pamięć radia lub wgraj oryginalny eksport CSV z bankami.') from None
    if len(raw) > 1024 * 1024:
        raise HTTPException(400, 'Baza banków jest za duża.')
    try:
        banks, channels = parse_backup_csv(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        raise HTTPException(409, 'Do wyboru banków potrzebny jest eksport AR-DV10 Connect zawierający banki i kanały.') from None
    titles = {bank.index: bank.title.strip() for bank in banks}
    rows = []
    for bank in range(40):
        programmed = [channel for channel in channels if channel.bank == bank and not channel.is_empty]
        eligible = [channel for channel in programmed if not channel.pass_flag]
        rows.append({'bank': bank, 'name': titles.get(bank) or 'Bank %02d' % bank,
                     'first_channel': eligible[0].channel if eligible else None,
                     'channels': len(programmed), 'scannable': sum(not channel.pass_flag for channel in programmed)})
    return {'banks': rows}


def validate_banks(value):
    if not isinstance(value, list) or not value or len(value) > 40:
        raise HTTPException(400, 'Zaznacz co najmniej jeden bank.')
    if any(type(bank) is not int or not 0 <= bank <= 39 for bank in value):
        raise HTTPException(400, 'Numer banku musi być liczbą od 00 do 39.')
    return sorted(set(value))


def scan_status(device):
    # Acknowledged button state; do not send RX while the receiver scans.
    return {'running': _running, 'banks': list(_selected_banks)}


def start_scan(device, banks):
    global _selected_banks, _running
    banks = validate_banks(banks)
    catalog = {row['bank']: row for row in bank_catalog()['banks']}
    if any(not catalog[bank]['scannable'] for bank in banks):
        raise HTTPException(400, 'Zaznacz banki zawierające kanały bez oznaczenia pomijania.')
    first = banks[0]
    # Enter memory mode first: BK is applied in the receiver's memory context.
    device.tune_memory_channel(first, catalog[first]['first_channel'])
    device.set_bank_link(banks if len(banks) > 1 else None)
    device.raw('MS', '%02d' % first)
    _selected_banks = banks
    _running = True
    return {'running': True, 'current_bank': first, 'banks': banks}


def stop_scan(device):
    global _running
    device.enter_vfo_mode('A')
    _running = False
    return {'running': False, 'current_bank': None, 'banks': list(_selected_banks)}


def check_request(request):
    if request.headers.get('x-memory-scan') != '1':
        raise HTTPException(403, 'Użyj przycisków skanowania w panelu sterowania.')
    origin = request.headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc.lower() != request.headers.get('host', '').lower():
            raise HTTPException(403, 'Sterowanie z innej strony jest zablokowane.')


async def run_command(function, *args):
    async with _command_lock:
        try:
            def execute():
                device = _get_device()
                # RX can take several seconds while the receiver is scanning.
                # Keep the complete operation exclusive, including MA continuation
                # lines, and avoid the protocol's short-timeout resync sequence.
                with device._chan.transaction():
                    previous = device._chan.timeout
                    device._chan.timeout = max(previous, 10.0)
                    try:
                        return function(device, *args)
                    finally:
                        device._chan.timeout = previous
            return await run_in_threadpool(execute)
        except DV10Error as exc:
            raise HTTPException(502, 'Radio odrzuciło polecenie: %s' % exc) from exc


@router.get('/api/memory/scan/banks')
def available_banks():
    return bank_catalog()


@router.get('/api/memory/scan')
async def current_scan():
    return await run_command(scan_status)


@router.post('/api/memory/scan/start')
async def api_start(request: Request):
    check_request(request)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 1024:
            raise HTTPException(413, 'Lista banków jest za długa.')
        raw.extend(chunk)
    import json
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'Niepoprawna lista banków.') from None
    banks = validate_banks(body.get('banks') if isinstance(body, dict) else None)
    return await run_command(start_scan, banks)


@router.post('/api/memory/scan/stop')
async def api_stop(request: Request):
    check_request(request)
    return await run_command(stop_scan)
