"""CSV replacement for the read-only viewer, exposed only by the control app."""
import csv
import importlib.util
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

router = APIRouter()
BASE = Path(os.environ.get('AOR_VIEWER_DATA_DIR', str(Path.home() / '.local/share/aor-dv10'))).expanduser()
VIEWER_SOURCE = Path(__file__).resolve().parents[3] / 'raspberry-viewer'
CHANNEL_PATH = BASE / 'channels.csv'
MAX_SIZE = 1024 * 1024
_write_lock = threading.Lock()
_spec = importlib.util.spec_from_file_location('viewer_channel_parser', VIEWER_SOURCE / 'channel_names.py')
_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parser)
_channel_names = _parser.ChannelNames(CHANNEL_PATH)


def channel_name_for(frequency_hz):
    with _write_lock:
        return _channel_names.load().get(frequency_hz)


def _validate(raw):
    if not raw:
        raise HTTPException(400, 'Wybierz plik CSV z częstotliwościami i nazwami.')
    if len(raw) > MAX_SIZE:
        raise HTTPException(413, 'Plik jest za duży. Maksymalny rozmiar to 1 MB.')
    try:
        names = _parser.parse_names(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError, csv.Error):
        raise HTTPException(400, 'Niepoprawny plik CSV. Wybierz eksport AR-DV10 Connect lub CSV frequency_mhz;name zapisany jako UTF-8.') from None
    if not names:
        raise HTTPException(400, 'Plik nie zawiera częstotliwości z nazwami. Poprzednia baza została zachowana.')
    return names


def _store(raw, names):
    with _write_lock:
        CHANNEL_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup_name = None
        if CHANNEL_PATH.exists():
            backup_dir = CHANNEL_PATH.parent / 'backups'
            backup_dir.mkdir(mode=0o700, exist_ok=True)
            backup_name = 'channels-%s-%s.csv' % (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'), uuid.uuid4().hex[:8])
            with (backup_dir / backup_name).open('xb') as backup:
                backup.write(CHANNEL_PATH.read_bytes())
                backup.flush()
                os.fsync(backup.fileno())
        descriptor, temporary = tempfile.mkstemp(prefix='.channels-upload-', dir=CHANNEL_PATH.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, CHANNEL_PATH)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {'frequencies': len(names), 'backup_created': backup_name is not None}


@router.get('/api/viewer/channels')
def channel_info():
    try:
        raw = CHANNEL_PATH.read_bytes()
    except FileNotFoundError:
        return {'frequencies': 0, 'available': False}
    names = _validate(raw)
    return {'frequencies': len(names), 'available': True}


@router.post('/api/viewer/channels')
async def upload_channels(request: Request):
    # A custom header prevents cross-site HTML form submissions. No CORS is enabled.
    if request.headers.get('x-viewer-upload') != '1':
        raise HTTPException(403, 'Wgrywanie jest dostępne z formularza w panelu sterowania.')
    origin = request.headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc.lower() != request.headers.get('host', '').lower():
            raise HTTPException(403, 'Wgrywanie z innej strony jest zablokowane.')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_SIZE:
            raise HTTPException(413, 'Plik jest za duży. Maksymalny rozmiar to 1 MB.')
        raw.extend(chunk)
    content = bytes(raw)
    names = _validate(content)
    return await run_in_threadpool(_store, content, names)
