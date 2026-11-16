(() => {
  'use strict';
  const banks = document.getElementById('memoryScanBanks');
  const start = document.getElementById('memoryScanStart');
  const stop = document.getElementById('memoryScanStop');
  const status = document.getElementById('memoryScanStatus');
  const refresh = document.getElementById('scanRefreshBanks');
  let busy = false;
  let remembered = [];
  try { remembered = JSON.parse(localStorage.getItem('aor.memoryScan.banks') || '[]'); } catch (_) {}
  if (!Array.isArray(remembered)) remembered = [];
  const choices = () => [...banks.querySelectorAll('input:checked')].map(input => Number(input.value));
  function update() {
    start.disabled = busy || !choices().length;
    stop.disabled = busy;
    refresh.disabled = busy;
  }
  function remember() {
    try { localStorage.setItem('aor.memoryScan.banks', JSON.stringify(choices())); } catch (_) {}
    update();
  }
  async function request(path, options) {
    const response = await fetch(path, {signal: AbortSignal.timeout(20000), ...options});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Nie udało się wykonać polecenia.');
    return result;
  }
  async function loadBanks() {
    busy = true; update();
    status.textContent = 'Wczytywanie banków…';
    try {
      const result = await request('/api/memory/scan/banks', {cache: 'no-store'});
      const selected = choices().length ? choices() : remembered;
      banks.replaceChildren();
      for (const bank of result.banks) {
        const label = document.createElement('label'); label.className = 'memory-scan-choice';
        const input = document.createElement('input'); input.type = 'checkbox'; input.value = bank.bank;
        input.disabled = !bank.scannable; input.checked = !!bank.scannable && selected.includes(bank.bank);
        const text = document.createElement('span'); text.textContent = `${String(bank.bank).padStart(2, '0')} · ${bank.name}`;
        const count = document.createElement('small');
        count.textContent = bank.channels ? `${bank.scannable} do skanowania / ${bank.channels} zapisanych` : 'Pusty bank';
        text.appendChild(count); label.append(input, text); banks.appendChild(label);
        input.addEventListener('change', remember);
      }
      if (!selected.length) { const first = banks.querySelector('input:not(:disabled)'); if (first) first.checked = true; }
      status.textContent = 'Wybierz banki i naciśnij Start skanowania.';
      remember();
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; update(); }
  }
  async function act(action) {
    if (busy) return;
    busy = true; update();
    status.textContent = action === 'start' ? 'Uruchamianie skanowania…' : 'Zatrzymywanie skanowania…';
    try {
      const result = await request('/api/memory/scan/' + action, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Memory-Scan': '1'},
        body: JSON.stringify({banks: choices()})
      });
      status.textContent = result.running ? 'Skanowanie banków: ' + result.banks.map(bank => String(bank).padStart(2, '0')).join(', ') : 'Skanowanie zatrzymane · VFO A';
      remember();
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; update(); }
  }
  document.getElementById('scanSelectAll').addEventListener('click', () => { banks.querySelectorAll('input:not(:disabled)').forEach(input => input.checked = true); remember(); });
  document.getElementById('scanSelectNone').addEventListener('click', () => { banks.querySelectorAll('input').forEach(input => input.checked = false); remember(); });
  refresh.addEventListener('click', loadBanks);
  start.addEventListener('click', () => act('start'));
  stop.addEventListener('click', () => act('stop'));
  loadBanks();
})();

