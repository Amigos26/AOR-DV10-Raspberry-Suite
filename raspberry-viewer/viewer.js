'use strict';
function renderStationLabel(element, label) {
  element.replaceChildren();
  element.hidden = !label;
  if (!label) return;
  for (const entry of String(label).split(' / ')) {
    const separator = entry.indexOf(' → ');
    const bank = separator < 0 ? entry : entry.slice(0, separator);
    const name = separator < 0 ? '' : entry.slice(separator + 3);
    const group = document.createElement('span');
    group.className = 'station-entry';
    for (const [caption, value] of [['Bank:', bank], ['Nazwa:', name]]) {
      if (!value) continue;
      const row = document.createElement('span');
      row.className = 'station-row';
      const key = document.createElement('small');
      key.className = 'station-key';
      key.textContent = caption;
      const text = document.createElement('span');
      text.className = 'station-value';
      text.textContent = value;
      row.append(key, text);
      group.appendChild(row);
    }
    element.appendChild(group);
  }
}

const frequency = document.getElementById('frequency');
const channel = document.getElementById('channel');
const connection = document.getElementById('connection');
const freshness = document.getElementById('freshness');
const audio = document.getElementById('audio');
const button = document.getElementById('listen');
const audioStatus = document.getElementById('audioStatus');
const volume = document.getElementById('volume');
let active = false;
let generation = 0;
let lastReception = null;

function showLastReception(hit) {
  lastReception = hit;
  frequency.textContent = (hit.frequency_hz / 1000000).toFixed(5);
  renderStationLabel(channel, hit.channel_name || '');
  channel.hidden = !hit.channel_name;
}

function receptionTime() {
  return lastReception ? 'Ostatni odbiór: ' + new Date(lastReception.received_at * 1000).toLocaleTimeString('pl-PL') : 'Oczekiwanie na pierwszy odebrany sygnał';
}

async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store', signal: AbortSignal.timeout(18000)});
    if (!response.ok) throw new Error('unavailable');
    const data = await response.json();
    document.getElementById('audioListenerCount').textContent = 'Słuchacze online: ' + (Number.isInteger(data.audio_listeners) ? data.audio_listeners : '—');
    if (!data.connected || Date.now() / 1000 - data.updated_at > 30) throw new Error('disconnected');
    if (data.last_received && Number.isFinite(data.last_received.frequency_hz)) showLastReception(data.last_received);
    connection.textContent = data.receiving ? '● Odbiór sygnału' : '● Oczekiwanie na odbiór';
    connection.className = 'online';
    freshness.textContent = receptionTime();
  } catch (_) {
    document.getElementById('audioListenerCount').textContent = 'Słuchacze online: —';
    connection.textContent = '● Brak aktualnych danych';
    connection.className = '';
    freshness.textContent = (lastReception ? receptionTime() + ' · ' : '') + 'Ponawianie połączenia…';
  } finally {
    setTimeout(refresh, 1000);
  }
}

function stop(message = 'Odsłuch wyłączony') {
  active = false;
  generation++;
  audio.pause();
  audio.removeAttribute('src');
  audio.load();
  button.setAttribute('aria-pressed', 'false');
  button.textContent = 'Słuchaj na żywo';
  audioStatus.textContent = message;
}
button.addEventListener('click', async () => {
  if (active) { stop(); return; }
  active = true;
  const attempt = ++generation;
  button.setAttribute('aria-pressed', 'true');
  button.textContent = 'Zatrzymaj odsłuch';
  audioStatus.textContent = 'Łączenie z dźwiękiem…';
  audio.src = '/audio.aac';
  try { await audio.play(); }
  catch (error) {
    if (active && generation === attempt) stop(error.name === 'NotAllowedError' ? 'Kliknij ponownie, aby uruchomić dźwięk.' : 'Dźwięk niedostępny. Spróbuj ponownie.');
  }
});
audio.addEventListener('playing', () => { if (active) audioStatus.textContent = 'Odsłuch na żywo · AAC 48 kb/s'; });
audio.addEventListener('waiting', () => { if (active) audioStatus.textContent = 'Buforowanie dźwięku…'; });
audio.addEventListener('error', () => { if (active) stop('Dźwięk niedostępny. Spróbuj ponownie.'); });
audio.addEventListener('ended', () => { if (active) stop('Połączenie zakończone. Włącz odsłuch ponownie.'); });
function updateVolume() {
  audio.volume = Number(volume.value);
  document.getElementById('volumeValue').value = Math.round(audio.volume * 100) + '%';
}
try {
  const saved = localStorage.getItem('radio-viewer-volume');
  if (saved !== null && Number.isFinite(Number(saved)) && Number(saved) >= 0 && Number(saved) <= 1) volume.value = saved;
} catch (_) {}
volume.addEventListener('input', () => {
  updateVolume();
  try { localStorage.setItem('radio-viewer-volume', volume.value); } catch (_) {}
});
window.addEventListener('pagehide', () => stop());
updateVolume();
refresh();
