/* Basic HL7 mappings keep incoming sources independent from outgoing targets. */
(() => {
  const fields = [
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
  const get = id => document.getElementById(id);
  const dirtyOutputs = new Set();
  let originalMappings = [];
  const pathPattern = /^([A-Z][A-Z0-9]{2})-([1-9]\d*)(?:\[([1-9]\d*)\])?(?:\.([1-9]\d*))?(?:\.([1-9]\d*))?$/;
  const paths = value => String(value || '').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean);
  const normalize = path => path.replace(/\[1\]/g,'');
  const overlaps = (a,b) => {a=normalize(a);b=normalize(b);return a===b || a.startsWith(b+'.') || b.startsWith(a+'.') || a.startsWith(b+'[') || b.startsWith(a+'[');};
  const walk = segments => (segments || []).flatMap(s=>[s,...walk(s.children)]);
  const matches = (field, source) => [source.source,...source.aliases || []].includes(field.source);

  function readExample(raw, locations) {
    const rows=String(raw || '').trim().split(/\r\n|[\r\n]/), separator=rows[0]?.[3] || '|';
    const encoding=rows[0]?.split(separator)[1] || '^~\\&';
    for(const path of paths(locations)) {
      const match=pathPattern.exec(path);if(!match)continue;
      const [,name,number,repetition,component,sub]=match;
      const row=rows.find(r=>r.startsWith(name+separator));if(!row)continue;
      let value=row.split(separator)[Number(number)-(name==='MSH'?1:0)];
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

  function setSchemaPath(schema, path, source) {
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
      if(sub){const parent=repetition.components[component] ||= {subcomponents:{}};parent.subcomponents ||= {};parent.subcomponents[sub]={source};}
      else repetition.components[component]={source};
    }
  }

  function insertSegment(segments, segment) {
    const order=['MSH','PID','PV1','ORC','OBR','SPM','OBX','NTE'];
    const position=order.indexOf(segment.name);
    const next=segments.findIndex(s=>order.indexOf(s.name)>position);
    segments.splice(next<0?segments.length:next,0,segment);
  }

  function applyMappings(definition, values, editedOutputs=[], savedMappings=[]) {
    const occupied=[];
    for(const field of fields) {
      const value=values[field.source] || {};
      for(const direction of ['from','to'])for(const path of paths(value[direction])) {
        if(!pathPattern.test(path))throw Error(`${field.label}: usa una ubicación como ORC-4.1 (${path}).`);
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
    const known=new Set(fields.map(f=>f.source));
    definition.inbound.mappings=existing.filter((m,i,list)=>!known.has(m.target) && list.findIndex(n=>n.target===m.target)===i);
    for(const field of fields.filter(f=>f.input)) {
      const value=values[field.source] || {}, locations=paths(value.from);
      if(locations.length)definition.inbound.mappings.push({...existing.find(m=>m.target===field.source),target:field.source,source:locations[0],alternatives:locations.slice(1),required:Boolean(value.required)});
    }
    const outbound=definition.outbound || {}, event=outbound.events?.RESULT_FINAL;
    if(!event)return definition;
    const schemas=[...new Set([outbound.schema,outbound.eventSchemas?.RESULT_FINAL,event.schema].filter(Boolean))];
    for(const field of fields.filter(f=>editedOutputs.includes(f.source))) {
      const targets=paths(values[field.source]?.to), old=[];
      for(const segment of walk(event.segments)){
        segment.fields=(segment.fields || []).filter(mapping=>{if(matches(mapping,field)){old.push(mapping.target);return false;}return true;});
      }
      for(const schema of schemas)for(const path of old)removeSchemaPath(schema,path);
      for(const target of targets){
        const name=target.split('-')[0];
        let segment=walk(event.segments).find(s=>s.name===name);
        if(!segment){segment={name,fields:[]};insertSegment(event.segments,segment);}
        segment.fields=segment.fields.filter(mapping=>!overlaps(mapping.target,target));
        segment.fields.push({target,source:field.source});
        for(const schema of schemas)setSchemaPath(schema,target,field.source);
      }
    }
    return definition;
  }

  function values() {
    return Object.fromEntries(fields.map(f=>[f.source,{from:f.input?get(f.input).value:'',to:get(f.output).value,required:f.input?get(f.input+'Required').checked:false}]));
  }
  function updateExamples() {
    const raw=window.savedInboundRawHL7 || '';
    for(const f of fields.filter(f=>f.input))get(f.input+'Example').textContent=raw ? (readExample(raw,get(f.input).value) || 'Sin dato en el ejemplo') : 'Genera una base desde un mensaje para ver el valor leído.';
  }
  function readOutbound(outbound) {
    const segments=walk(outbound?.events?.RESULT_FINAL?.segments);
    for(const field of fields)get(field.output).value=[...new Set(segments.flatMap(s=>(s.fields || []).filter(m=>matches(m,field)).map(m=>m.target)))].join(', ');
    dirtyOutputs.clear();
  }
  function load(profile) {
    originalMappings=structuredClone(profile.inbound?.mappings || []);
    for(const field of fields.filter(f=>f.input)){
      const mapping=originalMappings.find(m=>m.target===field.source);
      if(mapping)get(field.input).value=[mapping.source,...mapping.alternatives || []].filter(Boolean).join(', ');
      get(field.input+'Required').checked=Boolean(mapping?.required);
    }
    readOutbound(profile.outbound);updateExamples();
  }
  function reset() {
    originalMappings=[];dirtyOutputs.clear();
    for(const f of fields){if(f.input){get(f.input).value=f.from;get(f.input+'Required').checked=false;}get(f.output).value=f.to;}
    updateExamples();
  }
  function initialize() {
    const container=get('basicHL7Rows');if(!container)return;
    container.innerHTML=fields.map(f=>`<tr><th scope="row">${f.label}</th><td>${f.input?`<label class="sr-only" for="${f.input}">Leer ${f.label}</label><input id="${f.input}" value="${f.from}" placeholder="Ej. ORC-4.1"><label class="basic-required"><input id="${f.input}Required" type="checkbox"> Obligatorio al recibir</label><small id="${f.input}Example" class="hint"></small>`:'Se genera al procesar el examen.'}</td><td><label class="sr-only" for="${f.output}">Devolver ${f.label}</label><input id="${f.output}" value="${f.to}" placeholder="Vacío: no copiar este dato"></td></tr>`).join('');
    for(const field of fields){
      if(field.input){get(field.input).addEventListener('input',()=>{updateExamples();renderCanvas();});}
      get(field.output).addEventListener('input',()=>{dirtyOutputs.add(field.source);});
    }
    updateExamples();
  }
  window.basicHL7={fields,initialize,load,reset,readOutbound,updateExamples,readExample,applyMappings,
    apply:definition=>applyMappings(definition,values(),[...dirtyOutputs],originalMappings),
    outboundFields:()=>fields.filter(f=>f.input).flatMap(f=>paths(get(f.output).value).map(target=>({target,source:f.source}))),
  };
  initialize();
})();
