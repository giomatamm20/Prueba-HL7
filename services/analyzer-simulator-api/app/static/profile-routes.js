/* Pure operations shared by the visual editor and its regression tests. */
(() => {
  const pathPattern=/^([A-Z][A-Z0-9]{2})-([1-9]\d*)(?:\[([1-9]\d*)\])?(?:\.([1-9]\d*))?(?:\.([1-9]\d*))?$/;
  function flatten(nodes,parent=[],result=[]){(nodes||[]).forEach((node,index)=>{const path=[...parent,index];result.push({node,path,key:path.join('.'),depth:parent.length});flatten(node.children,path,result);});return result;}
  function syncSchema(schema,segments){
    if(schema?.formatVersion!==2)return;
    const oldSegments=schema.segments||[];
    schema.segments=flatten(segments).filter(s=>s.node.name).map(({node})=>{
      const old=oldSegments.find(s=>s.name===node.name),updated={...(old||{}),name:node.name,fields:{}};
      if(!old){updated.min=0;updated.max=node.repeatBy?'*':1;}
      if(node.repeatBy)updated.max='*';
      for(const mapping of node.fields||[]){
        const match=pathPattern.exec(mapping.target||'');if(!match||match[1]!==node.name)throw Error('Ruta HL7 no válida: '+mapping.target);
        const [,name,number,rep,component,sub]=match;if(name==='MSH'&&number==='2')continue;
        const index=Number(rep||1),previous=old?.fields?.[number],oldRep=previous?.repetitions?.find(r=>(r.index||1)===index),oldComponent=oldRep?.components?.[component||'1'];
        const original=sub?oldComponent?.subcomponents?.[sub]:oldComponent;
        const field=updated.fields[number]||={...(previous||{}),required:!!previous?.required,minRepetitions:previous?.minRepetitions??0,maxRepetitions:index,composite:!!component,repetitions:[]};
        field.composite ||= !!component;field.required ||= !!mapping.required;if(field.maxRepetitions!=='*')field.maxRepetitions=Math.max(Number(field.maxRepetitions)||1,index);
        let repetition=field.repetitions.find(r=>r.index===index);if(!repetition){repetition={index,components:{}};field.repetitions.push(repetition);}
        const rule={...(original||{}),required:mapping.required??original?.required??false};delete rule.const;delete rule.source;delete rule.transform;delete rule.example;delete rule.default;
        if(mapping.sourceType==='fixed')rule.const=String(mapping.value??'');else if(mapping.source)rule.source=mapping.source;
        if(mapping.transform)rule.transform=mapping.transform;if(mapping.default!==undefined)rule.default=mapping.default;
        if(sub){const parent=repetition.components[component]||={subcomponents:{}};parent.subcomponents[sub]=rule;}else repetition.components[component||'1']=rule;
      }
      // Encoding characters are emitted by the writer, outside editable routes.
      if(node.name==='MSH'&&old?.fields?.['2'])updated.fields['2']=structuredClone(old.fields['2']);
      return updated;
    });
  }
  function syncEventSchemas(definition,eventName){
    const event=definition.outbound?.events?.[eventName];if(!event)return definition;
    if(!event.schema&&definition.outbound?.schema&&Object.keys(definition.outbound.events).length>1)event.schema=structuredClone(definition.outbound.schema);
    const schema=event.schema||definition.outbound?.eventSchemas?.[eventName]||definition.outbound?.schema;
    syncSchema(schema,event.segments);
    return definition;
  }
  function applyRouteEdits(definition,edits){
    for(const edit of edits){
      const node=flatten(definition.outbound?.events?.[edit.event]?.segments).find(s=>s.key===edit.key)?.node,mapping=node?.fields?.[edit.index];
      if(!mapping)continue;
      if(edit.target!==undefined){const match=pathPattern.exec(edit.target);if(!match||match[1]!==node.name)throw Error('Ruta HL7 no válida: '+edit.target);if(node.fields.some((m,i)=>i!==edit.index&&m.target===edit.target))throw Error('Ruta duplicada: '+edit.target);mapping.target=edit.target;}
      if(edit.value!==undefined)mapping.value=edit.value;if(edit.source!==undefined)mapping.source=edit.source.trim();
      syncEventSchemas(definition,edit.event);
    }
    return definition;
  }
  window.HL7WorkshopModel={pathPattern,flatten,syncSchema,syncEventSchemas,applyRouteEdits};
})();
