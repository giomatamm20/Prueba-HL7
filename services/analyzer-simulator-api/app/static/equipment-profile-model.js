/* A single draft and revision own all equipment configuration. */
(() => {
  const clone=value=>structuredClone(value);
  class EquipmentDraft {
    constructor(request){this.request=request;this.profile=null;this.analyzerId=null;this.dirty=false;this.generation=0;this.version=0;this.loadSequence=0;this.previewSequence=0;}
    change(edit){if(!this.profile)return;edit(this.profile);this.generation++;this.dirty=true;}
    async load(analyzerId,{force=false}={}){
      if(this.dirty){if(analyzerId!==this.analyzerId)throw Error('Guarda los cambios del equipo actual antes de cambiar de equipo.');return false;}
      if(this.profile&&this.analyzerId===analyzerId&&!force)return false;
      const sequence=++this.loadSequence,generation=this.generation;
      const profile=await this.request(`/api/analyzers/${encodeURIComponent(analyzerId)}/equipment-profile`);
      if(sequence!==this.loadSequence||generation!==this.generation)return false;
      this.profile=profile;this.analyzerId=analyzerId;this.version=profile.version||0;this.dirty=false;this.generation++;return true;
    }
    async save(){
      const generation=this.generation,analyzerId=this.analyzerId;
      const saved=await this.request(`/api/analyzers/${encodeURIComponent(analyzerId)}/equipment-profile`,'PUT',{
        name:this.profile.name,inbound:clone(this.profile.inbound),outbound:clone(this.profile.outbound),catalog:clone(this.profile.catalog),expectedVersion:this.version});
      if(analyzerId!==this.analyzerId)return null;
      this.version=saved.version;
      if(generation===this.generation){this.profile=saved;this.dirty=false;this.generation++;}
      else{this.profile.id=saved.id;this.profile.version=saved.version;}
      return saved;
    }
    async preview(eventType='RESULT_FINAL',scenario='NORMAL'){
      const sequence=++this.previewSequence,generation=this.generation,analyzerId=this.analyzerId;
      try{
        const result=await this.request(`/api/analyzers/${encodeURIComponent(analyzerId)}/equipment-profile/preview`,'POST',{profile:clone(this.profile),eventType,scenario});
        if(sequence!==this.previewSequence||generation!==this.generation||analyzerId!==this.analyzerId)return null;
        return result;
      }catch(error){if(sequence===this.previewSequence&&generation===this.generation&&analyzerId===this.analyzerId)throw error;return null;}
    }
    async infer(rawMessage,direction='auto',eventType='RESULT_FINAL'){
      const generation=this.generation,analyzerId=this.analyzerId;
      const result=await this.request(`/api/analyzers/${encodeURIComponent(analyzerId)}/equipment-profile/infer`,'POST',{profile:clone(this.profile),rawMessage,direction,eventType});
      if(generation!==this.generation||analyzerId!==this.analyzerId)throw Error('El perfil cambió durante el análisis. Analiza de nuevo para conservar tus cambios.');
      return result;
    }
    apply(profile){this.profile=clone(profile);this.generation++;this.dirty=true;}
  }
  const nodes=(items,path=[])=> (items||[]).flatMap((node,index)=>[{node,key:[...path,index].join('.'),depth:path.length},...nodes(node.children,[...path,index])]);
  const groups=(study,path='')=>!study?[]:[{node:study,path},...(study.groups||[]).flatMap(g=>groups(g,path?path+'.'+g.id:g.id))];
  const pathPattern=/^[A-Z][A-Z0-9]{2}-[1-9]\d*(?:\[[1-9]\d*\])?(?:\.[1-9]\d*){0,2}$/;
  function editRoute(profile,eventName,key,index,patch){
    const node=nodes(profile.outbound.events[eventName].segments).find(n=>n.key===key)?.node;
    if(!node?.fields?.[index])throw Error('Selecciona una ruta existente.');
    const target=patch.target??node.fields[index].target;
    if(!pathPattern.test(target)||!target.startsWith(node.name+'-')||target==='MSH-1')throw Error('Ruta HL7 no válida: '+target);
    if(node.fields.some((f,i)=>i!==index&&f.target===target))throw Error('Esta ruta ya está ocupada.');
    Object.assign(node.fields[index],patch);
  }
  const equipmentChoices=items=>items.filter(a=>a.protocol==='HL7');
  window.EquipmentProfileModel={EquipmentDraft,nodes,groups,pathPattern,editRoute,equipmentChoices};
})();
