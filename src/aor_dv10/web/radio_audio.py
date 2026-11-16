from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()
log = logging.getLogger(__name__)


class RadioAudio:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.listeners = set()
        self.process = None
        self.task = None

    async def _stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.process:
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
        self.task = self.process = None

    async def _broadcast(self):
        try:
            while data := await self.process.stdout.read(1024):
                for queue in tuple(self.listeners):
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(data)
        finally:
            for queue in tuple(self.listeners):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(None)

    async def subscribe(self):
        async with self.lock:
            if not self.task or self.task.done():
                await self._stop()
                try:
                    self.process = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                        '-f', 'alsa', '-ac', '1', '-ar', '44100',
                        '-i', os.environ.get('AOR_AUDIO_DEVICE', 'plughw:CARD=Device,DEV=0'),
                        '-c:a', 'aac', '-profile:a', 'aac_low',
                        '-b:a', '48k', '-ar', '24000', '-ac', '1',
                        '-flush_packets', '1', '-f', 'adts', 'pipe:1',
                        stdout=asyncio.subprocess.PIPE,
                        stderr=None,
                    )
                    await asyncio.sleep(0.3)
                    if self.process.returncode is not None:
                        await self._stop()
                        raise HTTPException(503, 'Nie można otworzyć wejścia audio USB.')
                except OSError as exc:
                    await self._stop()
                    raise HTTPException(503, 'Przechwytywanie audio jest niedostępne.') from exc
                self.task = asyncio.create_task(self._broadcast())
            queue = asyncio.Queue(maxsize=8)
            self.listeners.add(queue)
            return queue

    async def unsubscribe(self, queue):
        async with self.lock:
            self.listeners.discard(queue)
            if not self.listeners:
                await self._stop()

    async def close(self):
        async with self.lock:
            await self._stop()
            self.listeners.clear()


radio_audio = RadioAudio()


@router.get('/api/audio/live.aac')
async def live_audio():
    queue = await radio_audio.subscribe()

    async def stream():
        try:
            while (data := await queue.get()) is not None:
                yield data
        finally:
            await radio_audio.unsubscribe(queue)

    return StreamingResponse(stream(), media_type='audio/aac', headers={
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        'X-Accel-Buffering': 'no',
    })
