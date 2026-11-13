const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const catalogue=JSON.parse(fs.readFileSync('static/ca.json','utf8'));
async function run(saved,navigatorLanguage='es-ES',storageFails=false){
 const storage=new Map(saved?[['slotmuse-language',saved]]:[]);
 const document={documentElement:{lang:'es'},body:{},title:'',createTreeWalker:()=>({nextNode:()=>false}),querySelectorAll:()=>[],addEventListener:()=>{}};
 const context=vm.createContext({console,navigator:{language:navigatorLanguage},NodeFilter:{SHOW_TEXT:4},document,localStorage:{getItem:key=>{if(storageFails)throw Error('Storage unavailable');return storage.get(key);},setItem:(key,value)=>{if(storageFails)throw Error('Storage unavailable');storage.set(key,value);}},fetch:async()=>({ok:true,json:async()=>catalogue})});
 vm.runInContext(fs.readFileSync('static/i18n.js','utf8'),context);
 await vm.runInContext('localizationReady',context);
 return {context,storage,document,eval:source=>vm.runInContext(source,context)};
}
(async()=>{
 const ca=await run('ca');
 assert.equal(ca.document.documentElement.lang,'ca');
 assert.equal(ca.eval("i18nText('Guía de configuración')"),'Guia de configuració');
 assert.equal(ca.eval("i18nText('Usuario o contraseña incorrectos.')"),'Usuari o contrasenya incorrectes.');
 assert.equal(ca.eval("i18nTemplate`<strong>Grupo ${'Nombre Materia Grupos'}</strong>`"),'<strong>Grup Nombre Materia Grupos</strong>');
 assert.equal(ca.eval("i18nTemplate`<option value=\"${'Grupos'}\">${'Nombre'}</option>`"),'<option value="Grupos">Nombre</option>');
 ca.eval("var labels=i18nLabels({title:['Planificación semanal'],days:['Lunes','Martes']})");
 assert.equal(ca.eval('labels.days[0]'),'Dilluns');
 assert.equal(ca.eval("new Date('2026-11-09T12:00:00').toLocaleDateString(uiLocale(),{weekday:'long'})"),'dilluns');
 ca.eval("setUILanguage('es')");
 assert.equal(ca.eval('labels.days[0]'),'Lunes');
 assert.equal(ca.eval('labels.title[0]'),'Planificación semanal');
 assert.equal(ca.storage.get('slotmuse-language'),'es');
 ca.eval("setUILanguage('unsupported')");
 assert.equal(ca.document.documentElement.lang,'es');
 const automatic=await run(undefined,'ca-ES');assert.equal(automatic.document.documentElement.lang,'ca');
 const remembered=await run('es','ca-ES');assert.equal(remembered.document.documentElement.lang,'es');
 const blocked=await run(undefined,'ca-ES',true);assert.equal(blocked.eval("i18nText('Guardar sesión')"),'Desar sessió');
 assert.equal(Object.values(catalogue).every(value=>typeof value==='string'&&value.length>0),true);
 assert.equal(fs.readFileSync('static/index.html','utf8').includes('class="language-select"'),false);
 assert.equal(fs.readFileSync('static/index.html','utf8').includes('class="language-trigger"'),true);
 console.log('Localization checks passed: locale, persistence, live labels, messages, flags and user-data isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
