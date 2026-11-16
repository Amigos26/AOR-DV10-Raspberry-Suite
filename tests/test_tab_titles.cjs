const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root,'src/aor_dv10/web/static/index.html'),'utf8');
const viewer = fs.readFileSync(path.join(root,'raspberry-viewer/viewer.js'),'utf8');
function control() {
  const context = vm.createContext({document:{title:''},window:{},translation:'AOR AR-DV10 – Panel sterowania'});
  context.t=()=>context.translation;
  vm.runInContext(html.slice(html.indexOf('function updateControlTabTitle('),html.indexOf('function renderStatus(')),context);
  return context;
}
test('control title follows the channel and receiver model',()=>{
  const c=control();c.updateControlTabTitle({connected:true,model:'AOR AR-DV1',channel_name:'DEMO BANK → DEMO FM',frequency_hz:145500000});
  assert.equal(c.document.title,'DEMO BANK → DEMO FM | AOR AR-DV1 – Panel sterowania');
  c.updateControlTabTitle({connected:true,model:'AOR AR-DV1',channel_name:'DEMO AIR → TOWER',frequency_hz:145575000});
  assert.ok(c.document.title.startsWith('DEMO AIR → TOWER |'));
});
test('control title handles missing names, disconnection and language changes',()=>{
  const c=control();c.updateControlTabTitle({connected:true,model:'AOR AR-DV10',frequency_hz:145500000});
  assert.ok(c.document.title.startsWith('145.50000 MHz |'));
  c.translation='AOR AR-DV10 – Web Panel';c.updateControlTabTitle();
  assert.ok(c.document.title.endsWith('– Web Panel'));
  c.updateControlTabTitle({connected:false,model:'AOR AR-DV1'});
  assert.equal(c.document.title,'AOR AR-DV1 – Web Panel');
});
test('listening title uses the held reception and keeps the listening page identity',()=>{
  const c=vm.createContext({document:{title:'AOR AR-DV1 · Odsłuch'}});
  vm.runInContext(viewer.slice(viewer.indexOf('const listeningTabBase'),viewer.indexOf('function renderStationLabel')),c);
  c.updateListeningTabTitle({channel_name:'DEMO BANK → DEMO FM',frequency_hz:145500000});
  assert.equal(c.document.title,'DEMO BANK → DEMO FM | AOR AR-DV1 · Odsłuch');
  c.updateListeningTabTitle({frequency_hz:150225000});
  assert.equal(c.document.title,'150.22500 MHz | AOR AR-DV1 · Odsłuch');
});
test('published browser scripts parse',()=>{
  for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) if(match[1].trim()) new vm.Script(match[1]);
  new vm.Script(viewer);
  new vm.Script(fs.readFileSync(path.join(root,'src/aor_dv10/web/static/memory_scan.js'),'utf8'));
  new vm.Script(fs.readFileSync(path.join(root,'src/aor_dv10/web/static/memory_editor.js'),'utf8'));
});
