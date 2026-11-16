(() => {
  const player = document.getElementById('radioAudioPlayer');
  const start = document.getElementById('radioAudioStart');
  const volume = document.getElementById('radioAudioVolume');
  const status = document.getElementById('radioAudioStatus');
  let active = false;
  let generation = 0;
  const activeKey = 'dv10.audio.enabled';
  let wanted = false;
  try { wanted = localStorage.getItem(activeKey) === 'true'; } catch (_) {}
  function rememberEnabled(value) {
    wanted = value;
    try { localStorage.setItem(activeKey, String(value)); } catch (_) {}
  }
  const volumeKey = 'dv10.audio.volume';
  try {
    const saved = localStorage.getItem(volumeKey);
    if (saved !== null && saved.trim() !== '') {
      const value = Number(saved);
      if (Number.isFinite(value) && value >= 0 && value <= 1) volume.value = String(value);
    }
  } catch (_) {}
  player.volume = Number(volume.value);
  volume.addEventListener('input', () => {
    player.volume = Number(volume.value);
    try { localStorage.setItem(volumeKey, volume.value); } catch (_) {}
  });
  function halt(message = 'Odsłuch wyłączony', remember = false) {
    if (remember) rememberEnabled(false);
    active = false;
    generation++;
    player.pause();
    player.removeAttribute('src');
    player.load();
    start.classList.remove('is-playing', 'needs-gesture');
    start.setAttribute('aria-pressed', 'false');
    start.setAttribute('aria-label', 'Włącz odsłuch');
    start.title = message === 'Odsłuch wyłączony' ? 'Włącz odsłuch radia' : message;
    status.textContent = message;
  }
  async function begin() {
    rememberEnabled(true);
    active = true;
    start.classList.remove('needs-gesture');
    const current = ++generation;
    start.classList.add('is-playing');
    start.setAttribute('aria-pressed', 'true');
    start.setAttribute('aria-label', 'Zatrzymaj odsłuch');
    start.title = 'Zatrzymaj odsłuch radia';
    status.textContent = 'Łączenie z wejściem audio…';
    player.src = '/api/audio/live.aac?t=' + Date.now();
    try { await player.play(); }
    catch (error) {
      if (active && generation === current) {
        if (error.name === 'NotAllowedError') {
          halt('Kliknij głośnik, aby wznowić odsłuch. Przeglądarka wymaga kliknięcia.');
          start.classList.add('needs-gesture');
          start.setAttribute('aria-label', 'Wznów odsłuch');
        } else {
          halt('Nie można uruchomić odsłuchu. Sprawdź kartę USB i spróbuj ponownie.');
        }
      }
    }
  }
  start.addEventListener('click', () => {
    if (active) halt(undefined, true);
    else begin();
  });
  player.addEventListener('playing', () => { if (active) status.textContent = 'Odsłuch na żywo · AAC 48 kb/s'; });
  player.addEventListener('waiting', () => { if (active) status.textContent = 'Buforowanie dźwięku…'; });
  player.addEventListener('error', () => { if (active) halt('Błąd odsłuchu. Sprawdź wejście USB i spróbuj ponownie.'); });
  player.addEventListener('ended', () => { if (active) halt('Połączenie audio zakończone. Włącz odsłuch ponownie.'); });
  window.addEventListener('pagehide', () => halt());
  window.addEventListener('pageshow', event => {
    if (event.persisted && wanted && !active) begin();
  });
  if (wanted) begin();
})();
