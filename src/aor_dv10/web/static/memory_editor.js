'use strict';
(() => {
  const root=document.getElementById('aor-channel-editor'), q=id=>root.querySelector('#aor-'+id);
  const clone=x=>JSON.parse(JSON.stringify(x)), same=(a,b)=>JSON.stringify(a)===JSON.stringify(b), pad=n=>String(n).padStart(2,'0');
  const entries=new Map();
  const modeNames={'0F0':'FM','0F1':'AM','000':'AUTO','010':'D-STAR','020':'YAESU','030':'ALINCO','040':'NXDN / D-CR','050':'P25','060':'dPMR','070':'DMR','080':'TETRA-DM','090':'TETRA-TC','0F2':'SAH','0F3':'SAL','0F4':'USB','0F5':'LSB','0F6':'CW'};
  const steps=[10,50,100,500,1000,2000,5000,6250,7500,8330,9000,10000,12500,15000,20000,25000,30000,50000,100000,500000];
  let selected=Number(new URLSearchParams(location.search).get('bank')||0), page=0, busy=false, loading=false, jobId=null, reviewAction=null, errors={};
  if(!Number.isInteger(selected)||selected<0||selected>39)selected=0;
  function message(text,error=false){q('message').textContent=text;q('message').classList.toggle('error',error);}
  function element(tag,props={}){const node=document.createElement(tag);Object.assign(node,props);return node;}
  function entry(){return entries.get(selected);}
  function toUi(row){return {...row,freq:row.frequency_hz===null?'':(row.frequency_hz/1e6).toFixed(5)};}
  function wire(row){const result={...row,frequency_hz:row.freq.trim()?Math.round(Number(row.freq.trim().replace(',','.'))*1e6):null};delete result.freq;return result;}
  function dirty(row,base){return !same(row,toUi(base));}
  function changes(){const e=entry();return e?e.draft.channels.reduce((n,c,i)=>n+dirty(c,e.base.channels[i]),0)+(e.draft.name!==e.base.name?1:0):0;}
  async function api(path,options={},timeout=90000){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(timeout),...options});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||'Nie udało się wykonać operacji.');return data;
  }
  function post(path,data){return api(path,{method:'POST',headers:{'Content-Type':'application/json','X-Memory-Editor':'1'},body:JSON.stringify(data)},15000);}
  function closeReview(){q('review').hidden=true;reviewAction=null;}
  function review(title,detail,label,action){q('review-title').textContent=title;q('review-detail').textContent=detail;q('review-confirm').textContent=label;reviewAction=action;q('review').hidden=false;}
  function backupControls(){
    const e=entry(),current=q('backups').value;q('backups').replaceChildren();
    for(const b of e?.base.backups||[])q('backups').append(element('option',{value:b.id,textContent:new Date(b.created_at).toLocaleString('pl-PL')+' · '+b.kind}));
    if(!q('backups').options.length)q('backups').append(element('option',{value:'',textContent:'Brak kopii'}));

    q('download').hidden=!q('backups').value;q('download').href='/api/editor/backups/'+q('backups').value+'/file';
    q('restore').disabled=busy||loading||!q('backups').value||!!e?.base.protect;
  }
  function metadata(){
    const e=entry(),locked=busy||loading;
    q('bank').disabled=locked;q('read').disabled=locked;q('bank-name').disabled=locked||!e||e.base.protect;
    q('save-bank').disabled=locked||!e||e.base.protect;q('prev').disabled=locked||page===0;q('next').disabled=locked||page===8;
    q('backups').disabled=locked;q('restore').disabled=locked||!q('backups').value||!!e?.base.protect;
    q('bank-info').textContent=e?'50 pozycji · '+e.draft.channels.filter(c=>c.freq.trim()).length+' zapisanych kanałów'+(e.base.protect?' · bank chroniony':''):'';
    q('dirty').textContent=changes()?changes()+' niezapisane zmiany':'';
    root.querySelectorAll('tr[data-slot]').forEach(tr=>{
      if(!e){tr.querySelectorAll('input,select,button').forEach(c=>c.disabled=true);return;}
      const i=Number(tr.dataset.slot),row=e.draft.channels[i],changed=dirty(row,e.base.channels[i]),protectedRow=e.base.channels[i].protect||e.base.protect;
      tr.classList.toggle('dirty',changed);const label=tr.querySelector('.row-state');label.textContent=errors[i]||(protectedRow?'Chroniony':changed?'Niezapisana zmiana':row.freq?'Odczytany z radia':'Pusta pozycja');label.classList.toggle('error',!!errors[i]);
      tr.querySelectorAll('input,select,button').forEach(c=>c.disabled=locked||protectedRow);
      tr.querySelector('[data-save]').disabled=locked||protectedRow||!changed;
    });
  }
  function renderRows(){
    const e=entry(),body=q('rows');body.replaceChildren();if(!e){const tr=element('tr'),td=element('td',{textContent:'Bank nie jest jeszcze odczytany. Kliknij „Odczytaj bank z radia”.'});td.colSpan=6;tr.append(td);body.append(tr);q('page-info').textContent='';metadata();return;}
    for(let i=page*6;i<Math.min(50,page*6+6);i++){
      const row=e.draft.channels[i],tr=element('tr');tr.dataset.slot=i;tr.append(element('td',{textContent:pad(selected)+'-'+pad(i)}));
      for(const field of ['name','freq','mode']){
        const cell=element('td');let input;
        if(field==='mode'){input=element('select');for(const [code,name]of Object.entries(modeNames))input.append(element('option',{value:code,textContent:name}));}
        else{input=element('input',{type:'text',value:row[field],autocomplete:'off'});if(field==='name')input.maxLength=12;else{input.inputMode='decimal';input.className='freq';input.placeholder='—';}}
        input.value=row[field];input.setAttribute('aria-label',(field==='name'?'Nazwa':field==='freq'?'Częstotliwość MHz':'Tryb')+' kanału '+pad(selected)+'-'+pad(i));
        input.addEventListener(field==='mode'?'change':'input',()=>{closeReview();row[field]=input.value;delete errors[i];metadata();});cell.append(input);
        if(field==='name')cell.append(element('small',{className:'row-state'}));
        if(field==='mode'){
          const label=element('label',{className:'step-label',textContent:'Krok (kHz)'}),step=element('select');
          for(const hz of steps)step.append(element('option',{value:String(hz),textContent:(hz/1000).toLocaleString('pl-PL',{maximumFractionDigits:2})}));
          if(!steps.includes(row.step_hz))step.append(element('option',{value:String(row.step_hz),textContent:String(row.step_hz/1000)}));
          step.value=String(row.step_hz);step.setAttribute('aria-label','Krok kanału '+pad(selected)+'-'+pad(i));step.addEventListener('change',()=>{closeReview();row.step_hz=Number(step.value);metadata();});label.append(step);cell.append(label);
        }
        tr.append(cell);
      }
      const skipCell=element('td'),skipLabel=element('label',{className:'skip'}),skip=element('input',{type:'checkbox',checked:row.skip});skip.setAttribute('aria-label','Pomijaj kanał '+pad(selected)+'-'+pad(i)+' podczas skanowania');skip.addEventListener('change',()=>{closeReview();row.skip=skip.checked;metadata();});skipLabel.append(skip);skipCell.append(skipLabel);tr.append(skipCell);
      const cell=element('td'),buttons=element('div',{className:'row-actions'}),save=element('button',{type:'button',textContent:'Zapisz'}),clear=element('button',{type:'button',textContent:'Wyczyść'});save.dataset.save='';save.addEventListener('click',()=>saveRows(i));clear.addEventListener('click',()=>{closeReview();Object.assign(row,{freq:'',frequency_hz:null,name:'',mode:'0F0',skip:false,protect:false});delete errors[i];renderRows();message('Pozycja '+pad(selected)+'-'+pad(i)+' przygotowana do usunięcia. Zatwierdź przyciskiem Zapisz.');});buttons.append(save,clear);cell.append(buttons);tr.append(cell);body.append(tr);
    }
    q('page-info').textContent=`Pozycje ${page*6+1}–${Math.min(50,page*6+6)} z 50`;metadata();
  }
  function render(){q('bank').value=String(selected);q('bank-name').value=entry()?.draft.name||'';renderRows();backupControls();}
  async function loadBank(discard=false){
    loading=true;metadata();q('connection').textContent='Odczyt banku '+pad(selected)+'…';
    try{
      if(discard||!entries.has(selected)){
        const base=await api('/api/editor/bank/'+selected+'/read',{method:'POST',headers:{'Content-Type':'application/json','X-Memory-Editor':'1'},body:'{}'});entries.set(selected,{base,draft:{name:base.name,channels:base.channels.map(toUi)}});
        q('bank').options[selected].textContent=pad(selected)+' · '+(base.name||'Bez nazwy');
      }
      errors={};render();q('connection').textContent='Odczytano bank '+pad(selected);document.title='Bank '+pad(selected)+' · '+(entry().base.name||'Bez nazwy')+' | Edytor kanałów AOR';
      history.replaceState(null,'','/memory-editor?bank='+selected);
    }catch(error){message('Odczyt nieudany: '+error.message,true);q('connection').textContent='Brak odczytu';}
    finally{loading=false;metadata();}
  }
  function validate(indices){
    errors={};const e=entry();let invalid=false;
    if(!/^[\x20-\x7e]{0,12}$/.test(e.draft.name)){message('Nazwa banku: maksymalnie 12 znaków bez polskich liter.',true);return false;}
    for(const i of indices){const row=e.draft.channels[i],text=row.freq.trim().replace(',','.');let error='';
      if(!text&&(row.name.trim()||row.skip))error='Podaj częstotliwość';
      else if(text&&(!/^\d+(\.\d{1,5})?$/.test(text)||Number(text)<.1||Number(text)>1300))error='Zakres: 0,10000–1300 MHz';
      else if(!/^[\x20-\x7e]{0,12}$/.test(row.name))error='Nazwa: do 12 znaków bez polskich liter';
      if(error){errors[i]=error;invalid=true;}else{row.freq=text?Number(text).toFixed(5):'';row.name=row.name.trim();}
    }
    if(invalid){page=Math.floor(Number(Object.keys(errors)[0])/6);renderRows();message('Popraw oznaczone pola przed zapisem.',true);return false;}return true;
  }
  async function saveRows(target){
    if(busy||loading||!entry())return;const indices=target===null?Array.from({length:50},(_,i)=>i):[target];if(!validate(indices))return;
    const send=()=>start('/api/editor/bank/'+selected+'/save',{revision:entry().base.revision,channel:target,name:target===null?entry().draft.name:entry().base.name,channels:indices.map(i=>wire(entry().draft.channels[i]))},target);
    if(target!==null)return send();
    const filled=entry().draft.channels.filter(c=>c.freq).length;
    review('Zapis całego banku '+pad(selected)+' · '+(entry().draft.name||'Bez nazwy'),`${filled} kanałów i ${50-filled} pustych pozycji. Najpierw kopia, potem zapis oraz kontrola wszystkich 50 pozycji. Puste pozycje usuną odpowiadające im kanały w tym banku.`,'Utwórz kopię i zapisz bank',send);
  }
  async function start(path,payload,target){
    closeReview();busy=true;metadata();
    const draft=entry()?clone(entry().draft):null;
    try{const job=await post(path,payload);jobId=job.id;try{localStorage.setItem('aor-editor-job',jobId);}catch(_){}await pollJob(job.id,draft,target);}
    catch(error){message(error.message,true);busy=false;metadata();}
  }
  async function pollJob(identity,draft,target){
    busy=true;q('operation').hidden=false;q('interrupt').disabled=false;metadata();
    try{
      let job;
      while(true){
        job=await api('/api/editor/jobs/'+identity,{},15000);q('progress').max=job.total;q('progress').value=job.completed;q('progress-text').textContent=job.phase+' · '+job.completed+'/'+job.total;
        if(job.phase.startsWith('Sprawdzanie')||job.completed>=job.total)q('interrupt').disabled=true;
        if(!['queued','running'].includes(job.state))break;
        await new Promise(resolve=>setTimeout(resolve,600));
      }
      selected=job.bank;await loadBank(true);
      const e=entry();
      if(draft&&e&&(job.state!=='done'||target!==null)){
        e.draft=clone(draft);if(job.state==='done'&&target!==null)e.draft.channels[target]=toUi(e.base.channels[target]);
      }
      if(job.state==='done')message((target===null?'Bank '+pad(selected):'Kanał '+pad(selected)+'-'+pad(target))+' zapisany i sprawdzony.'+(job.warning?' '+job.warning:' Opisy w obu panelach odświeżone.')+(job.scan_stopped?' Skanowanie możesz wznowić w panelu sterowania.':''));
      else if(job.state==='cancelled')message('Zapis przerwany po bieżącej pozycji. Niezapisane zmiany pozostają w tabeli.');
      else message('Operacja nie została zakończona: '+(job.error||job.phase)+(job.backup_id?' Kopia banku jest dostępna poniżej.':''),true);
      try{localStorage.removeItem('aor-editor-job');}catch(_){}
    }catch(error){message('Nie udało się pobrać wyniku: '+error.message+' Zapis może nadal trwać. Odśwież stronę, aby sprawdzić operację.',true);}
    finally{busy=false;jobId=null;q('operation').hidden=true;render();}
  }
  q('bank').addEventListener('change',()=>{selected=Number(q('bank').value);page=0;errors={};closeReview();render();message(entry()?'Przywrócono roboczy widok banku '+pad(selected)+'.':'Wybierz „Odczytaj bank z radia”. Odczyt zatrzyma skanowanie i przełączy radio do VFO A.');});
  q('bank-name').addEventListener('input',()=>{if(entry()){closeReview();entry().draft.name=q('bank-name').value;metadata();}});
  q('save-bank').addEventListener('click',()=>saveRows(null));q('prev').addEventListener('click',()=>{page--;renderRows();});q('next').addEventListener('click',()=>{page++;renderRows();});
  q('read').addEventListener('click',()=>{const read=async()=>{closeReview();await loadBank(true);message('Bank '+pad(selected)+' ponownie odczytany z radia.');};if(changes())review('Odczytać bank ponownie?','Ponowny odczyt zastąpi niezapisane zmiany wybranego banku.','Odrzuć zmiany i odczytaj',read);else read();});
  q('backups').addEventListener('change',()=>{q('download').hidden=!q('backups').value;q('download').href='/api/editor/backups/'+q('backups').value+'/file';metadata();});
  q('restore').addEventListener('click',()=>{if(!entry()||!q('backups').value)return;const id=q('backups').value;review('Przywrócić bank '+pad(selected)+' z kopii?','Zastąpi to kanały i nazwę wybranego banku. Przed przywróceniem powstanie nowa kopia jego bieżącej zawartości.','Przywróć kopię banku',()=>start('/api/editor/bank/'+selected+'/restore',{revision:entry().base.revision,backup_id:id},null));});
  q('review-cancel').addEventListener('click',closeReview);q('review-confirm').addEventListener('click',()=>{if(reviewAction)reviewAction();});
  q('interrupt').addEventListener('click',async()=>{if(!jobId)return;q('interrupt').disabled=true;try{await post('/api/editor/jobs/'+jobId+'/cancel',{});}catch(error){message(error.message,true);}});
  window.addEventListener('beforeunload',event=>{if(busy||[...entries.values()].some(e=>e.draft.name!==e.base.name||e.draft.channels.some((c,i)=>dirty(c,e.base.channels[i])))){event.preventDefault();event.returnValue='';}});
  async function initialize(){
    loading=true;metadata();
    try{
      const catalog=await api('/api/editor/banks');for(const b of catalog.banks)q('bank').append(element('option',{value:String(b.bank),textContent:pad(b.bank)+' · '+(b.name||'Bez nazwy')}));
      api('/api/status',{},15000).then(status=>{q('model').textContent=status.model||'AOR';if(status.device_family==='DV10'){for(const code of ['090','0F2','0F3'])delete modeNames[code];root.querySelectorAll('select[aria-label^="Tryb"]').forEach(select=>[...select.options].filter(option=>!modeNames[option.value]).forEach(option=>option.remove()));}}).catch(()=>{});
      const active=await api('/api/editor/jobs/active');let remembered;try{remembered=localStorage.getItem('aor-editor-job');}catch(_){}
      if(active.id||remembered){jobId=active.id||remembered;selected=active.bank??selected;loading=false;await pollJob(jobId,null,null);}
      else{loading=false;render();q('connection').textContent='Gotowy do odczytu';message('Wybierz bank i kliknij „Odczytaj bank z radia”. Odczyt zatrzyma skanowanie i przełączy radio do VFO A.');}
    }catch(error){message(error.message,true);loading=false;metadata();}
  }
  initialize();
})();

