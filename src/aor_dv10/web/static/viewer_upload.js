(() => {
  'use strict';
  const form = document.getElementById('viewerCsvForm');
  const fileInput = document.getElementById('viewerCsvFile');
  const submit = document.getElementById('viewerCsvSubmit');
  const status = document.getElementById('viewerCsvStatus');
  let busy = false;
  let submitted = false;
  function updateButton() { submit.disabled = busy || !fileInput.files.length; }
  fileInput.addEventListener('change', updateButton);
  async function currentInfo() {
    try {
      const response = await fetch('/api/viewer/channels', {cache: 'no-store'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Nie można odczytać obecnej bazy.');
      if (!busy && !submitted) status.textContent = result.available ? `Obecna baza: ${result.frequencies} częstotliwości z nazwami.` : 'Nie wgrano jeszcze bazy nazw.';
    } catch (error) { if (!busy && !submitted) status.textContent = error.message; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const file = fileInput.files[0];
    if (busy || !file) return;
    if (file.size > 1024 * 1024) { status.textContent = 'Plik jest za duży. Maksymalny rozmiar to 1 MB.'; return; }
    busy = true;
    submitted = true;
    updateButton();
    fileInput.disabled = true;
    status.textContent = 'Sprawdzanie i wgrywanie pliku…';
    try {
      const response = await fetch('/api/viewer/channels', {
        method: 'POST',
        headers: {'Content-Type': 'text/csv', 'X-Viewer-Upload': '1'},
        body: file
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Nie udało się wgrać pliku.');
      status.textContent = `Wgrano ${result.frequencies} częstotliwości z nazwami. Podgląd odczyta je automatycznie.` + (result.backup_created ? ' Poprzednia baza została zachowana jako kopia.' : '');
      fileInput.value = '';
    } catch (error) {
      status.textContent = error.message === 'Failed to fetch' ? 'Brak potwierdzenia zapisu. Sprawdź połączenie i odśwież panel, aby zobaczyć obecną bazę.' : error.message;
    } finally {
      busy = false;
      fileInput.disabled = false;
      updateButton();
    }
  });
  currentInfo();
})();
