/* Basic HL7 mappings keep incoming sources independent from outgoing targets. */
(() => {
  const baseFields = [
    {source:'patient.id', label:'Identificador del paciente', input:'profilePatientId', output:'basicPatientId', from:'PID-3.1', to:'PID-3'},
    {source:'patient.name', label:'Nombre del paciente', input:'profilePatientName', output:'basicPatientName', from:'PID-5', to:'PID-5'},
    {source:'order.id', label:'Identificador de la orden', input:'profileOrderId', output:'basicOrderId', from:'ORC-2.1, OBR-2.1', to:'ORC-2, OBR-2', aliases:['study.orderId']},
    {source:'order.fillerId', label:'Identificador del laboratorio (opcional)', input:'profileFillerId', output:'basicFillerId', from:'ORC-3.1, OBR-3.1', to:'ORC-3'},
    {source:'sample.id', label:'Identificador de muestra / barcode', input:'profileSampleId', output:'basicSampleId', from:'SPM-2.1, OBR-3.1', to:'SPM-2, OBR-3'},
    {source:'test.code', label:'Código del examen', input:'profileTestCode', output:'basicTestCode', from:'OBR-4.1', to:'OBR-4.1', aliases:['study.code']},
    {source:'test.name', label:'Nombre del examen', input:'profileTestName', output:'basicTestName', from:'OBR-4.2', to:'OBR-4.2', aliases:['study.name']},
    {source:'result.code', label:'Código del resultado', output:'profileResultCode', to:'OBX-3.1'},
    {source:'result.value', label:'Valor del resultado', output:'profileResultValue', to:'OBX-5', aliases:['result.hl7Value']},
    {source:'result.unit', label:'Unidad del resultado', output:'profileResultUnit', to:'OBX-6'},
  ];
  const fields=[...baseFields];
  let selectedEvent='RESULT_FINAL', loadedOutbound={}, stagedEvents={};
  const get = id => document.getElementById(id);
  const escapeHTML=value=>String(value ?? '').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const additionalLabels={'patient.birthDate':'Fecha de nacimiento','patient.sex':'Sexo','patient.address':'Dirección',
    'sample.code':'Tipo de muestra: código','sample.name':'Tipo de muestra: nombre','visit.number':'Número de visita',
    'visit.type':'Tipo de visita','order.priority':'Prioridad','order.clinicalInfo':'Información clínica',
    'order.control':'Control de orden','order.status':'Estado de orden','patient.visitType':'Tipo de visita','patient.visitNumber':'Número de visita',
    'study.counter':'Número del estudio','result.valueType':'Tipo de dato del resultado','result.name':'Nombre del resultado',
    'result.subId':'Subidentificador del resultado','result.referenceRange':'Rango de referencia','result.abnormalFlag':'Indicador de anormalidad',
    'result.status':'Estado del resultado','result.note':'Nota del resultado','resultDocument.pdfBase64':'Documento PDF'};
  const semanticSources={PATIENT_IDENTIFIER:'patient.id',FAMILY_NAME:'patient.name',GIVEN_NAME:'patient.name',
    BIRTH_DATE:'patient.birthDate',ADMINISTRATIVE_SEX:'patient.sex',PLACER_ORDER_NUMBER:'order.id',FILLER_ORDER_NUMBER:'order.fillerId',
    SPECIMEN_IDENTIFIER:'sample.id',SPECIMEN_TYPE_CODE:'sample.code',SPECIMEN_TYPE_NAME:'sample.name',TEST_CODE:'test.code',TEST_NAME:'test.name',PATIENT_CLASS:'visit.type'};
  function extraField(source,label,index,mappingTarget=source) {
    const readable=!source.startsWith('inbound.') && !/^(result|system|analyzer|counter|resultDocument|study)\b/.test(source);
    return {source,label:label || additionalLabels[source] || (source.startsWith('inbound.')?'Copiar '+source.slice(8):source.startsWith('customFields.')?'Dato propio: '+source.slice(13):source),
      input:readable?'extraHL7Input'+index:null,output:'extraHL7Output'+index,from:'',to:'',mappingTarget,extra:true};
  }
  function fieldDefinitions(definition,values={}) {
    const result=[...fields];
    const add=(source,label,target)=>{if(source && !result.some(f=>f.source===source))result.push(extraField(source,label,result.length,target));};
    for(const row of definition.inbound?.editorFields || [])add(row.source,row.label,row.mappingTarget);
    for(const mapping of definition.inbound?.mappings || []){
      const source=baseFields.some(f=>f.source===mapping.target)||additionalLabels[mapping.target]||mapping.target.startsWith('customFields.')?mapping.target:'customFields.'+mapping.target;
      add(source,null,mapping.target);
    }
    for(const event of Object.values(definition.outbound?.events || {}))for(const segment of walk(event.segments))for(const mapping of segment.fields || []){
      if(mapping.source && !/^(system|analyzer)\./.test(mapping.source) && mapping.source!=='counter' && !baseFields.some(f=>matches(mapping,f)))add(mapping.source);
    }
    for(const source of Object.keys(values))add(source);
    return result;
  }
  const dirtyOutputs = new Set();
  let originalMappings = [];
  const pathPattern = /^([A-Z][A-Z0-9]{2})-([1-9]\d*)(?:\[([1-9]\d*)\])?(?:\.([1-9]\d*))?(?:\.([1-9]\d*))?$/;
  const paths = value => String(value || '').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean);
  const normalize = path => path.replace(/\[1\]/g,'');
  const overlaps = (a,b) => {a=normalize(a);b=normalize(b);return a===b || a.startsWith(b+'.') || b.startsWith(a+'.') || a.startsWith(b+'[') || b.startsWith(a+'[');};
  const walk = segments => (segments || []).flatMap(s=>[s,...walk(s.children)]);
  const matches = (field, source) => [source.source,...source.aliases || []].includes(field.source);

  function normalizeMessage(raw) {
    let text=String(raw || '').trim().replace(/\r\n|\n/g,'\r');
    const separator=(text[3] || '|').replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const endings=String.raw`(?:\\r\\n|\\r|\\n|/r/n|/r|/n)`;
    return text.replace(new RegExp(endings+`(?=[A-Z0-9]{3}${separator})`,'g'),'\r').replace(new RegExp(endings+'$'),'\r');
  }
  function readExample(raw, locations) {
    const rows=normalizeMessage(raw).split(/\r/), separator=rows[0]?.[3] || '|';
    const encoding=rows[0]?.split(separator)[1] || '^~\\&';
    for(const path of paths(locations)) {
      const match=pathPattern.exec(path);if(!match)continue;
      const [,name,number,repetition,component,sub]=match;
      const row=rows.find(r=>r.startsWith(name+separator));if(!row)continue;
      let value=row.split(separator)[Number(number)-(name==='MSH'?1:0)];
      if(!repetition && !component && !sub){if(value)return value;continue;}
      value=value?.split(encoding[1])[Number(repetition || 1)-1];
      if(component)value=value?.split(encoding[0])[Number(component)-1];
      if(sub)value=value?.split(encoding[3])[Number(sub)-1];
      if(value)return value;
    }
    return '';
  }

  function removeSchemaPath(schema, path) {
    if(schema?.formatVersion!==2)return;
    const [,name,number,rep,component,sub]=pathPattern.exec(path);
    for(const segment of schema.segments || []) {
      if(segment.name!==name)continue;
      const field=segment.fields?.[number];if(!field)continue;
      if(!component){delete segment.fields[number];continue;}
      const repetition=field.repetitions?.find(r=>(r.index || 1)===Number(rep || 1));
      const rules=repetition?.components;if(!rules)continue;
      if(sub){delete rules[component]?.subcomponents?.[sub];if(!Object.keys(rules[component]?.subcomponents || {}).length)delete rules[component];}
      else delete rules[component];
      field.repetitions=field.repetitions.filter(r=>Object.keys(r.components || {}).length);
      if(!field.repetitions.length)delete segment.fields[number];
    }
  }

  function setSchemaPath(schema, path, source, transform) {
    if(schema?.formatVersion!==2)return;
    const [,name,number,rep,component,sub]=pathPattern.exec(path);
    let segment=schema.segments.find(s=>s.name===name);
    if(!segment){segment={name,min:1,max:1,fields:{}};insertSegment(schema.segments,segment);}
    const field=segment.fields[number] ||= {required:false,minRepetitions:0,maxRepetitions:1,composite:false,repetitions:[]};
    const index=Number(rep || 1);
    let repetition=field.repetitions.find(r=>(r.index || 1)===index);
    if(!repetition){repetition={index,components:{}};field.repetitions.push(repetition);}
    field.maxRepetitions=Math.max(Number(field.maxRepetitions) || 1,index);
    if(!component){
      repetition.components=source==='patient.name' ? {'1':{source,transform:'component1'},'2':{source,transform:'component2'}} : {'1':{source}};
      field.composite=source==='patient.name';
    }else{
      field.composite=true;
      if(sub){const parent=repetition.components[component] ||= {subcomponents:{}};parent.subcomponents ||= {};parent.subcomponents[sub]={source,...(transform?{transform}:{})};}
      else repetition.components[component]={source,...(transform?{transform}:{})};
    }
  }

  function insertSegment(segments, segment) {
    const order=['MSH','PID','PV1','ORC','OBR','SPM','OBX','NTE'];
    const position=order.indexOf(segment.name);
    if(position<0){segments.push(segment);return;}
    const next=segments.findIndex(s=>order.indexOf(s.name)>position);
    segments.splice(next<0?segments.length:next,0,segment);
  }

  function applyMappings(definition, values, editedOutputs=[], savedMappings=[], eventName='RESULT_FINAL') {
    const configuredFields=fieldDefinitions(definition,values);
    const occupied=[];
    for(const field of configuredFields) {
      const value=values[field.source] || {};
      for(const direction of ['from','to'])for(const path of paths(value[direction])) {
        if(!pathPattern.test(path))throw Error(`${field.label}: usa el formato SEG-campo[repetición].componente (${path}).`);
        if(direction==='to'){
          const previous=occupied.find(p=>overlaps(path,p.path));
          if(previous)throw Error(`La salida ${path} está asignada a ${previous.label} y ${field.label}. Elige un campo distinto para cada dato.`);
          occupied.push({path,label:field.label});
        }
      }
      if(field.input && value.required && !paths(value.from).length)throw Error(`${field.label}: indica de dónde se debe leer el dato obligatorio.`);
    }
    definition.inbound ||= {};
    const existing=[...(definition.inbound.mappings || []),...savedMappings];
    const known=new Set(configuredFields.filter(f=>Object.hasOwn(values,f.source)).map(f=>f.mappingTarget || f.source));
    definition.inbound.mappings=existing.filter((m,i,list)=>!known.has(m.target) && list.findIndex(n=>n.target===m.target)===i);
    for(const field of configuredFields.filter(f=>f.input && Object.hasOwn(values,f.source))) {
      const value=values[field.source] || {}, locations=paths(value.from),target=field.mappingTarget || field.source;
      if(locations.length)definition.inbound.mappings.push({...existing.find(m=>m.target===target),target,source:locations[0],alternatives:locations.slice(1),required:Boolean(value.required)});
    }
    definition.inbound.editorFields=configuredFields.filter(f=>f.extra).map(({source,label,mappingTarget})=>({source,label,mappingTarget}));
    const outbound=definition.outbound || {}, event=outbound.events?.[eventName];
    if(!event)return definition;
    if(editedOutputs.length && Object.keys(outbound.events || {}).length>1 && !event.schema && !outbound.eventSchemas?.[eventName] && outbound.schema)event.schema=structuredClone(outbound.schema);
    const schemas=[...new Set([event.schema || outbound.eventSchemas?.[eventName] || outbound.schema].filter(Boolean))];
    for(const field of configuredFields.filter(f=>editedOutputs.includes(f.source))) {
      const targets=paths(values[field.source]?.to), old=[], previous=[];
      for(const segment of walk(event.segments)){
        segment.fields=(segment.fields || []).filter(mapping=>{if(matches(mapping,field)){old.push(mapping.target);previous.push(mapping);return false;}return true;});
      }
      for(const schema of schemas)for(const path of old)removeSchemaPath(schema,path);
      for(const target of targets){
        const name=target.split('-')[0];
        let segment=walk(event.segments).find(s=>s.name===name);
        if(!segment){segment={name,fields:[]};insertSegment(event.segments,segment);}
        segment.fields=segment.fields.filter(mapping=>!overlaps(mapping.target,target));
        const prior=previous.find(m=>normalize(m.target)===normalize(target));
        const transform=prior?.transform || (field.source==='patient.name' && /\.[12]$/.test(target)?'component'+target.slice(-1):undefined);
        const mapping={...(prior || {}),target,source:prior?.source || field.source,...(transform?{transform}:{})};
        segment.fields.push(mapping);
        for(const schema of schemas)setSchemaPath(schema,target,mapping.source,transform);
      }
    }
    return definition;
  }

  function values() {
    return Object.fromEntries(fields.map(f=>[f.source,{from:f.input?(get(f.input)?.value || ''):'',to:get(f.output)?.value || '',required:f.input?Boolean(get(f.input+'Required')?.checked):false}]));
  }
  function updateExamples() {
    const raw=window.savedInboundRawHL7 || '';
    for(const f of fields.filter(f=>f.input))if(get(f.input+'Example'))get(f.input+'Example').textContent=raw ? (readExample(raw,get(f.input).value) || 'Sin dato en el ejemplo') : 'Sin mensaje de ejemplo.';
  }
  function renderRows(current={}) {
    const container=get('basicHL7Rows');if(!container)return;
    container.innerHTML=fields.map(f=>{
      const v=current[f.source] || {from:f.from,to:f.to}, label=escapeHTML(f.label);
      return `<tr><th scope="row">${label}${f.extra?`<small>${escapeHTML(f.source)}</small>`:''}</th><td>${f.input?`<label class="sr-only" for="${f.input}">Leer ${label}</label><input id="${f.input}" value="${escapeHTML(v.from || '')}" placeholder="SEG-campo[repetición].componente"><label class="basic-required"><input id="${f.input}Required" type="checkbox" ${v.required?'checked':''}> Obligatorio al recibir</label><small id="${f.input}Example" class="hint"></small>`:escapeHTML(f.source.startsWith('inbound.')?'Se copia del mensaje real: '+f.source.slice(8):'Se obtiene al generar el mensaje.')}</td><td><label class="sr-only" for="${f.output}">Devolver ${label}</label><input id="${f.output}" value="${escapeHTML(v.to || '')}" placeholder="Vacío: no copiar este dato"><small id="${f.output}Details" class="hint"></small></td></tr>`;
    }).join('');
    for(const field of fields){
      if(field.input)get(field.input).addEventListener('input',updateExamples);
      get(field.output).addEventListener('input',()=>dirtyOutputs.add(field.source));
    }
    updateExamples();showOutputDetails();
  }
  function showOutputDetails() {
    for(const field of fields){
      const mappings=walk(loadedOutbound.events?.[selectedEvent]?.segments).flatMap(s=>s.fields || []).filter(m=>matches(m,field));
      if(get(field.output+'Details'))get(field.output+'Details').textContent=mappings.filter(m=>m.transform).map(m=>`${m.target}: ${m.transform==='component1'?'primer componente':m.transform==='component2'?'segundo componente':m.transform}`).join(' · ');
    }
  }
  function fillOutputs(outbound) {
    const segments=walk(outbound?.events?.[selectedEvent]?.segments);
    for(const field of fields)if(get(field.output))get(field.output).value=[...new Set(segments.flatMap(s=>(s.fields || []).filter(m=>matches(m,field)).map(m=>m.target)))].join(', ');
    dirtyOutputs.clear();showOutputDetails();
  }
  function readOutbound(outbound) {
    const current=values(), discovered=fieldDefinitions({outbound});
    if(discovered.length!==fields.length){fields.splice(0,fields.length,...discovered);renderRows(current);}
    loadedOutbound=structuredClone(outbound || {});stagedEvents={};
    const eventNames=Object.keys(loadedOutbound.events || {});
    selectedEvent=eventNames.includes('RESULT_FINAL')?'RESULT_FINAL':eventNames[0] || 'RESULT_FINAL';
    if(get('basicHL7Event')){get('basicHL7Event').innerHTML=eventNames.map(name=>`<option value="${escapeHTML(name)}">${escapeHTML(name)} · ${escapeHTML(loadedOutbound.events[name].messageType || '')}</option>`).join('') || '<option value="RESULT_FINAL">RESULT_FINAL (sin salida configurada)</option>';get('basicHL7Event').value=selectedEvent;}
    fillOutputs(loadedOutbound);
  }
  function selectEvent(name) {
    try {
      if(dirtyOutputs.size){
        const updated=applyMappings({inbound:{mappings:originalMappings},outbound:structuredClone(loadedOutbound)},values(),[...dirtyOutputs],originalMappings,selectedEvent);
        loadedOutbound=updated.outbound;stagedEvents[selectedEvent]=structuredClone(loadedOutbound.events[selectedEvent]);
      }
      selectedEvent=name;fillOutputs(loadedOutbound);
      if(typeof markProtocolEdited==='function')markProtocolEdited();
    }catch(error){if(get('basicHL7Event'))get('basicHL7Event').value=selectedEvent;if(get('basicHL7FieldStatus'))get('basicHL7FieldStatus').textContent=error.message;}
  }
  function suggestedPaths(schema) {
    const result={};
    for(const segment of schema?.segments || [])for(const [number,field] of Object.entries(segment.fields || {}))for(const rep of field.repetitions || [])for(const [component,rule] of Object.entries(rep.components || {})){
      const source=rule.source || semanticSources[rule.semantic];if(!source)continue;
      const path=source==='patient.name'?`${segment.name}-${number}`:`${segment.name}-${number}[${rep.index || 1}]${field.composite?'.'+component:''}`;
      if(!result[source])result[source]=path;
    }
    return result;
  }
  function suggestInbound(schema) {
    const suggestions=suggestedPaths(schema);
    for(const field of fields.filter(f=>f.input))if(suggestions[field.source] && (!get(field.input).value || get(field.input).value===field.from))get(field.input).value=suggestions[field.source];
    updateExamples();
  }
  function load(profile) {
    originalMappings=structuredClone(profile.inbound?.mappings || []);
    fields.splice(0,fields.length,...baseFields);
    const discovered=fieldDefinitions(profile);fields.splice(0,fields.length,...discovered);
    const suggestions=suggestedPaths(profile.inbound?.schema), current={};
    for(const field of fields){
      const mapping=originalMappings.find(m=>m.target===(field.mappingTarget || field.source));
      current[field.source]={from:mapping?[mapping.source,...mapping.alternatives || []].filter(Boolean).join(', '):suggestions[field.source] || '',required:Boolean(mapping?.required),to:''};
    }
    renderRows(current);readOutbound(profile.outbound);updateExamples();
  }
  function reset() {
    originalMappings=[];dirtyOutputs.clear();loadedOutbound={};stagedEvents={};selectedEvent='RESULT_FINAL';
    fields.splice(0,fields.length,...baseFields);renderRows();
    if(get('basicHL7Event'))get('basicHL7Event').innerHTML='<option value="RESULT_FINAL">RESULT_FINAL · plantilla inicial ORU</option>';
  }
  function addFieldFromForm() {
    try{
      const selected=get('basicHL7NewSource').value;
      const key=get('basicHL7NewKey').value.trim();
      if(selected==='custom' && !/^[A-Za-z][A-Za-z0-9_]*$/.test(key))throw Error('Escribe una clave con letras, números o guion bajo, empezando por una letra.');
      const source=selected==='custom'?'customFields.'+key:selected;
      if(fields.some(f=>f.source===source))throw Error('Este dato ya está en la tabla.');
      const current=values();fields.push(extraField(source,get('basicHL7NewLabel').value.trim(),fields.length));
      renderRows(current);get('basicHL7FieldStatus').textContent='Dato añadido. Configura sus rutas y guarda el perfil.';
      if(typeof markProtocolEdited==='function')markProtocolEdited();
    }catch(error){get('basicHL7FieldStatus').textContent=error.message;}
  }
  function initialize() {if(get('basicHL7Rows'))reset();}
  window.basicHL7={fields,initialize,load,reset,readOutbound,selectEvent,suggestInbound,suggestedPaths,addFieldFromForm,updateExamples,readExample,normalizeMessage,applyMappings,currentEvent:()=>selectedEvent,
    apply:definition=>{
      for(const [name,event] of Object.entries(stagedEvents)){definition.outbound ||= {};definition.outbound.events ||= {};definition.outbound.events[name]=structuredClone(event);}
      return applyMappings(definition,values(),[...dirtyOutputs],originalMappings,selectedEvent);
    },
    outboundFields:()=>fields.filter(f=>f.input).flatMap(f=>paths(get(f.output)?.value || '').map(target=>({target,source:f.source}))),
  };
  initialize();
})();
