/* Catalog definitions are data. No scripts, expressions or clinical rules run here. */
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let revisions = [], analyzers = [], catalogId = null, savedRevision = null;
let draft = {name:'Nuevo catálogo', studies:[]}, selectedStudy = -1, editingParameter = null, dirty = false;
const currentStudy = () => draft.studies[selectedStudy];
function status(message, error=false) { $('status').textContent=message; $('status').classList.toggle('error',error); }
async function request(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
function changed(message='Cambios en el editor; guarde una nueva revisión para usarlos en órdenes nuevas.') { dirty=true; status(message); }
function nodes(node=currentStudy(), prefix='') {
  if (!node) return [];
  return [{path:prefix, node}, ...(node.groups || []).flatMap(g => nodes(g, prefix ? `${prefix}.${g.id}` : g.id))];
}
function nodeAt(path) { return nodes().find(n => n.path===path)?.node; }
function renderStudies() {
  $('studySelect').innerHTML=draft.studies.map((s,i)=>`<option value="${i}">${escapeHTML(s.code)} — ${escapeHTML(s.name)}</option>`).join('');
  $('studySelect').value=String(selectedStudy);
  $('template').innerHTML=draft.studies.map(s=>`<option value="${escapeHTML(s.code)}">${escapeHTML(s.code)}</option>`).join('');
  $('revisionInfo').textContent=savedRevision ? `Revisión ${savedRevision.version} · ${savedRevision.id}. Guardar crea otra revisión; las órdenes existentes conservan su revisión.` : 'Catálogo nuevo, aún sin revisión guardada.';
  renderStudy();
}
function renderStudy() {
  const study=currentStudy();
  $('studyCode').value=study?.code || ''; $('studyName').value=study?.name || ''; $('studyAliases').value=(study?.aliases || []).join(', ');
  $('applyStudy').disabled=!study;
  const options=nodes().map(n=>`<option value="${escapeHTML(n.path)}">${escapeHTML(n.path || 'Raíz del estudio')}</option>`).join('');
  $('groupParent').innerHTML=options; $('parameterGroup').innerHTML=options;
  $('groupsList').innerHTML=''; $('parametersList').innerHTML='';
  for (const item of nodes()) {
    if (item.path) {
      const row=document.createElement('div'); row.className='item';
      const title=document.createElement('span'); title.textContent=`${item.path} × ${item.node.count ?? 1}`;
      const edit=document.createElement('button'); edit.className='secondary'; edit.textContent='Editar';
      edit.onclick=()=>{const parts=item.path.split('.');$('groupId').value=parts.pop();$('groupParent').value=parts.join('.');$('groupName').value=item.node.name;$('groupCount').value=item.node.count ?? 1;};
      const remove=document.createElement('button'); remove.className='secondary'; remove.textContent='Quitar';
      remove.onclick=()=>{const parts=item.path.split('.'),id=parts.pop(),parent=nodeAt(parts.join('.'));parent.groups=parent.groups.filter(g=>g.id!==id);changed();renderStudy();};
      row.append(title,edit,remove);$('groupsList').append(row);
    }
    (item.node.parameters || []).forEach((p,index)=>{
      const row=document.createElement('div');row.className='item';
      const title=document.createElement('span');title.textContent=`${item.path ? item.path+'.' : ''}${p.code} (${p.valueType})${!p.generator ? ' — configurar generador' : ''}`;
      const edit=document.createElement('button');edit.textContent='Editar';edit.onclick=()=>editParameter(item.path,index);
      const up=document.createElement('button');up.textContent='↑';up.className='secondary';up.disabled=index===0;
      up.onclick=()=>{const list=item.node.parameters;[list[index-1],list[index]]=[list[index],list[index-1]];changed();renderStudy();};
      const down=document.createElement('button');down.textContent='↓';down.className='secondary';down.disabled=index===(item.node.parameters.length-1);
      down.onclick=()=>{const list=item.node.parameters;[list[index+1],list[index]]=[list[index],list[index+1]];changed();renderStudy();};
      const remove=document.createElement('button');remove.textContent='×';remove.className='secondary';remove.onclick=()=>{item.node.parameters.splice(index,1);changed();renderStudy();};
      row.append(title,edit,up,down,remove);$('parametersList').append(row);
    });
  }
  const scenarios=Object.keys(study?.scenarios || {});
  $('scenarioSelect').innerHTML='<option value="">Seleccione…</option>'+scenarios.map(s=>`<option>${escapeHTML(s)}</option>`).join('');
  $('previewScenario').innerHTML=[...new Set(['NORMAL', ...draft.studies.flatMap(s=>Object.keys(s.scenarios || {}))])].map(s=>`<option>${escapeHTML(s)}</option>`).join('');
  $('scenarioSummary').textContent=JSON.stringify(study?.scenarios || {},null,2);
  clearParameter();
}
function clearParameter() {
  editingParameter=null;$('parameterTitle').textContent='Nuevo parámetro';
  ['parameterCode','parameterName','parameterUnit','parameterReference','parameterNote','generatorValues'].forEach(id=>$(id).value='');
  $('parameterGroup').value='';$('parameterType').value='NM';$('parameterFlag').value='';$('parameterStatus').value='F';
  $('generatorKind').value='numeric';$('generatorMin').value='1';$('generatorMax').value='10';$('generatorDecimals').value='1';renderGenerator();
}
function renderGenerator() { const numeric=$('generatorKind').value==='numeric';$('numericFields').hidden=!numeric;$('valueFields').hidden=numeric; }
function encodeValue(value) { return typeof value==='object' && value!==null ? [value.code,value.name,value.system].join('^') : String(value); }
function editParameter(path,index) {
  const p=nodeAt(path).parameters[index];editingParameter={path,index};$('parameterTitle').textContent=`Editar parámetro: ${p.code}`;
  $('parameterGroup').value=path;$('parameterType').value=p.valueType;$('parameterCode').value=p.code;$('parameterName').value=p.name;
  $('parameterUnit').value=p.unit || '';$('parameterReference').value=p.referenceRange || '';$('parameterNote').value=p.note || '';
  $('parameterFlag').value=p.abnormalFlag || '';$('parameterStatus').value=p.status || 'F';
  const g=p.generator || {kind:p.valueType==='NM'?'numeric':'fixed'};
  $('generatorKind').value=g.kind;$('generatorMin').value=g.min ?? 1;$('generatorMax').value=g.max ?? 10;$('generatorDecimals').value=g.decimals ?? 1;
  $('generatorValues').value=g.kind==='fixed' && g.value!==undefined ? encodeValue(g.value) : (g.values || []).map(encodeValue).join('\n');renderGenerator();
}
function generatorFromForm() {
  const kind=$('generatorKind').value, type=$('parameterType').value;
  if (kind==='numeric') {
    if (type!=='NM') throw Error('Seleccione NM para un generador numérico.');
    if (['generatorMin','generatorMax','generatorDecimals'].some(id=>$(id).value==='')) throw Error('Complete mínimo, máximo y decimales.');
    return {kind,min:Number($('generatorMin').value),max:Number($('generatorMax').value),decimals:Number($('generatorDecimals').value)};
  }
  const parse=value=>{
    if (type==='NM') { if (!value.trim() || !Number.isFinite(Number(value))) throw Error('Valor NM inválido.');return Number(value); }
    if (['CE','CWE'].includes(type)) { const parts=value.split('^');if(parts.length!==3 || parts.some(p=>!p.trim()))throw Error('Use código^descripción^sistema para CE/CWE.');return {code:parts[0],name:parts[1],system:parts[2]}; }
    return value;
  };
  const values=$('generatorValues').value.split('\n').filter(v=>v.trim()).map(parse);
  if(!values.length)throw Error('Declare al menos un valor de prueba.');
  if(kind==='fixed' && values.length!==1)throw Error('Valor fijo requiere exactamente una línea.');
  return kind==='fixed'?{kind,value:values[0]}:{kind,values};
}
function applyStudy() {
  const study=currentStudy();if(!study)throw Error('Cree o seleccione un estudio.');
  study.code=$('studyCode').value.trim();study.name=$('studyName').value.trim();study.aliases=$('studyAliases').value.split(',').map(s=>s.trim()).filter(Boolean);
}
function syncDraft() { draft.name=$('catalogName').value.trim();if(currentStudy())applyStudy();return draft; }
function newStudy() { if(currentStudy())applyStudy();draft.studies.push({code:'NUEVO-'+(draft.studies.length+1),name:'Nuevo estudio',parameters:[],groups:[]});selectedStudy=draft.studies.length-1;changed();renderStudies(); }
async function reloadRevisions(selectId) {
  revisions=await request('/api/catalogs');$('revision').innerHTML=revisions.map(r=>`<option value="${r.id}">${escapeHTML(r.name)} · v${r.version}</option>`).join('');
  if(selectId)$('revision').value=selectId;
}
function loadRevision() {
  const revision=revisions.find(r=>r.id===$('revision').value);if(!revision)return;
  savedRevision=revision;catalogId=revision.catalogId;draft=structuredClone({name:revision.name,studies:revision.studies});selectedStudy=0;dirty=false;
  $('catalogName').value=draft.name;renderStudies();status('Revisión cargada. Los cambios crean una nueva revisión al guardar.');
}
async function saveCatalog(copy=false) {
  syncDraft();const path=copy || !catalogId ? '/api/catalogs':`/api/catalogs/${catalogId}/revisions`;
  const saved=await request(path,'POST',draft);await reloadRevisions(saved.id);loadRevision();status(`Guardada revisión ${saved.version}. Asóciela al equipo para usarla en órdenes nuevas.`);
}
function renderAssignment() {
  const analyzer=analyzers.find(a=>a.id===$('analyzer').value);
  const revision=revisions.find(r=>r.id===analyzer?.catalogRevisionId);
  $('assignmentInfo').textContent=analyzer ? `Asignado: ${revision ? revision.name+' v'+revision.version : 'catálogo inicial / modo compatible'}. Política: ${analyzer.unknownStudyPolicy || 'compatible'}.` : 'Cree un equipo HL7 desde Configurar equipo.';
}
async function refreshAnalyzers() {
  const selected=$('analyzer').value;
  analyzers=(await request('/api/analyzers')).filter(a=>a.protocol==='HL7' && !['PACS','ANAPATH'].includes(a.profile));
  $('analyzer').innerHTML=analyzers.map(a=>`<option value="${escapeHTML(a.id)}">${escapeHTML(a.name)}</option>`).join('');
  if(analyzers.some(a=>a.id===selected))$('analyzer').value=selected;
  renderAssignment();
}
function bind(id,action) { $(id).addEventListener('click',async()=>{try{await action();}catch(error){status(error.message,true);}}); }
bind('loadRevision',loadRevision);
bind('newCatalog',()=>{catalogId=null;savedRevision=null;draft={name:'Nuevo catálogo',studies:[]};$('catalogName').value=draft.name;selectedStudy=-1;newStudy();});
bind('saveCatalog',()=>saveCatalog());bind('cloneCatalog',()=>saveCatalog(true));bind('newStudy',newStudy);
bind('removeStudy',()=>{draft.studies.splice(selectedStudy,1);selectedStudy=Math.min(selectedStudy,draft.studies.length-1);changed();renderStudies();});
bind('applyStudy',()=>{applyStudy();changed();renderStudies();});bind('newParameter',clearParameter);
bind('addGroup',()=>{
  applyStudy();
  const parent=nodeAt($('groupParent').value);if(!parent)throw Error('Seleccione un estudio.');
  const id=$('groupId').value.trim();if(!id || id.includes('.'))throw Error('Use un identificador de grupo sin puntos.');
  parent.groups ||= [];let group=parent.groups.find(g=>g.id===id);
  if(!group){group={id,parameters:[],groups:[]};parent.groups.push(group);}
  group.name=$('groupName').value.trim();group.count=Number($('groupCount').value);changed();renderStudy();
});
bind('applyParameter',()=>{
  applyStudy();
  const target=nodeAt($('parameterGroup').value);if(!target)throw Error('Seleccione un estudio y un grupo.');
  const parameter={code:$('parameterCode').value.trim(),name:$('parameterName').value.trim(),valueType:$('parameterType').value,
    unit:$('parameterUnit').value,referenceRange:$('parameterReference').value,abnormalFlag:$('parameterFlag').value,status:$('parameterStatus').value,
    note:$('parameterNote').value,generator:generatorFromForm()};
  if(!parameter.code || !parameter.name)throw Error('Complete código y descripción.');
  if(editingParameter){const source=nodeAt(editingParameter.path);source.parameters.splice(editingParameter.index,1);}
  target.parameters ||= [];
  if(editingParameter && editingParameter.path===$('parameterGroup').value)target.parameters.splice(editingParameter.index,0,parameter);else target.parameters.push(parameter);
  changed();renderStudy();
});
function scenario() {
  const study=currentStudy(),name=$('scenarioName').value.trim();if(!study || !name || name==='NORMAL')throw Error('Defina un nombre de escenario distinto de NORMAL.');
  study.scenarios ||= {};study.scenarios[name] ||= {parameters:{},groups:{}};return study.scenarios[name];
}
bind('scenarioParameter',()=>{
  applyStudy();
  const override=scenario(),path=[$('parameterGroup').value,$('parameterCode').value.trim()].filter(Boolean).join('.');
  if(!nodeAt($('parameterGroup').value)?.parameters.some(p=>p.code===$('parameterCode').value.trim()))throw Error('Seleccione un parámetro existente con Editar.');
  override.parameters[path]=generatorFromForm();changed();$('scenarioSummary').textContent=JSON.stringify(currentStudy().scenarios,null,2);
  const name=$('scenarioName').value;renderStudy();$('scenarioSelect').value=name;
});
bind('scenarioGroup',()=>{
  applyStudy();
  const path=[$('groupParent').value,$('groupId').value.trim()].filter(Boolean).join('.');if(!nodeAt(path) || !path)throw Error('Seleccione un grupo existente con Editar.');
  scenario().groups[path]=Number($('groupCount').value);changed();renderStudy();
});
bind('removeScenario',()=>{if(currentStudy()?.scenarios)delete currentStudy().scenarios[$('scenarioName').value];changed();renderStudy();});
bind('inferCatalog',async()=>{
  const suggestions=await request('/api/catalogs/infer','POST',{rawMessage:$('oruExample').value});
  if(currentStudy())applyStudy();draft.studies.push(...suggestions.studies);selectedStudy=draft.studies.length-suggestions.studies.length;changed(suggestions.warning);renderStudies();
});
bind('previewCatalog',async()=>{
  syncDraft();const data=await request('/api/catalogs/preview','POST',{catalog:draft,scenario:$('previewScenario').value});
  $('previewRows').innerHTML=data.results.map(r=>`<tr><td>${escapeHTML(r.studyCode)}</td><td>${escapeHTML(r.sampleId)}</td><td>${escapeHTML(r.subId)}<br>${escapeHTML(r.parentGroupId)}</td><td>${escapeHTML(r.code)} — ${escapeHTML(r.name)}</td><td>${escapeHTML(r.valueType)}</td><td>${escapeHTML(encodeValue(r.value))}</td><td>${escapeHTML(r.unit)}<br>${escapeHTML(r.referenceRange)}</td></tr>`).join('');
  $('previewMessage').textContent=data.message.replace(/\r/g,'\n');$('previewDialog').showModal();
});
bind('closePreview',()=>$('previewDialog').close());
bind('refreshAnalyzers',refreshAnalyzers);
bind('assignCatalog',async()=>{
  if(!savedRevision || dirty)throw Error('Guarde la revisión actual antes de asociarla.');
  const analyzerId=$('analyzer').value;if(!analyzerId)throw Error('Seleccione un analizador HL7.');
  const assigned=await request(`/api/analyzers/${encodeURIComponent(analyzerId)}/catalog`,'PUT',{revisionId:savedRevision.id,
    unknownStudyPolicy:$('policy').value,genericStudyCode:$('template').value,configureOutbound:$('configureOutbound').checked});
  analyzers=analyzers.map(a=>a.id===assigned.id?assigned:a);renderAssignment();status('Revisión asociada. Las órdenes nuevas usarán estas definiciones.');
  if(window.parent!==window)window.parent.postMessage({type:'catalog-assigned'},window.location.origin);
});
bind('exportJson',()=>{$('catalogJson').value=JSON.stringify(syncDraft(),null,2);});
bind('importJson',()=>{const value=JSON.parse($('catalogJson').value);if(!value.name || !Array.isArray(value.studies))throw Error('Defina name y studies.');draft=value;$('catalogName').value=draft.name;selectedStudy=0;changed();renderStudies();});
bind('loadBacteriology',()=>{
  if(currentStudy())applyStudy();
  const coded=(code,name)=>({code,name,system:'SIMULATED'});
  draft.studies.push({code:'CULTIVO',name:'Cultivo sintético y antibiograma',parameters:[
    {code:'CULTURE',name:'Resultado de cultivo',valueType:'ST',generator:{kind:'fixed',value:'Crecimiento sintético'}}],groups:[
    {id:'aislamiento',name:'Aislamiento',count:2,parameters:[{code:'ORG',name:'Microorganismo de prueba',valueType:'CWE',generator:{kind:'sequence',values:[coded('ORG-A','Microorganismo sintético A'),coded('ORG-B','Microorganismo sintético B')]}}],
     groups:[{id:'sensibilidad',name:'Sensibilidad de prueba',count:2,parameters:[
       {code:'AB',name:'Antibiótico de prueba',valueType:'CE',generator:{kind:'sequence',values:[coded('AB-A','Antibiótico sintético A'),coded('AB-B','Antibiótico sintético B')]}},
       {code:'CONC',name:'Concentración de prueba',valueType:'NM',unit:'unidad-prueba',generator:{kind:'numeric',min:1,max:8,decimals:1}},
       {code:'INTERP',name:'Interpretación de prueba',valueType:'ST',generator:{kind:'choice',values:['S','I','R']},note:'Interpretación sintética declarada; sin regla clínica'}]}]}]});
  selectedStudy=draft.studies.length-1;changed();renderStudies();
});
$('studySelect').onchange=()=>{if(currentStudy())applyStudy();selectedStudy=Number($('studySelect').value);renderStudy();};
$('scenarioSelect').onchange=()=>{$('scenarioName').value=$('scenarioSelect').value;};
$('generatorKind').onchange=renderGenerator;$('analyzer').onchange=renderAssignment;
['catalogName','studyCode','studyName','studyAliases'].forEach(id=>$(id).addEventListener('input',()=>{dirty=true;}));
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
window.addEventListener('message',event=>{
  if(event.origin===window.location.origin && event.data?.type==='refresh-analyzers')refreshAnalyzers().catch(error=>status(error.message,true));
});
async function initialize() {
  await reloadRevisions();await refreshAnalyzers();loadRevision();renderAssignment();
}
initialize().catch(error=>status(error.message,true));
