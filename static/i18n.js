/* Presentation-only localization. Template values and stored school data stay unchanged. */
let uiLanguage;
try { uiLanguage=localStorage.getItem('slotmuse-language'); } catch {}
if(!['es','ca'].includes(uiLanguage))uiLanguage=(navigator.language||'es').toLowerCase().startsWith('ca')?'ca':'es';
let catalanMessages={}, messagePattern=null;
const staticText=new Map(),staticAttributes=[],labelProxies=new WeakMap();
function uiLocale(){return uiLanguage==='ca'?'ca-ES':'es-ES';}
function i18nText(value){
 const source=String(value??'');
 if(uiLanguage!=='ca'||!messagePattern)return source;
 if(Object.prototype.hasOwnProperty.call(catalanMessages,source))return catalanMessages[source];
 return source.replace(messagePattern,key=>catalanMessages[key]);
}
function i18nTemplate(parts,...values){
 // Only literal segments are translated. Dynamic values are already escaped by callers.
 return parts.reduce((html,part,index)=>html+i18nText(part)+(index<values.length?values[index]:''),'');
}
function i18nLabels(value){
 if(!value||typeof value!=='object')return typeof value==='string'?i18nText(value):value;
 if(!labelProxies.has(value))labelProxies.set(value,new Proxy(value,{get(target,key,receiver){return i18nLabels(Reflect.get(target,key,receiver));}}));
 return labelProxies.get(value);
}
function applyStaticTranslations(){
 document.documentElement.lang=uiLanguage;
 document.title=i18nText('Slotmuse · Planificación docente');
 for(const [node,source] of staticText)if(node.isConnected)node.nodeValue=i18nText(source);
 for(const [node,name,source] of staticAttributes)if(node.isConnected)node.setAttribute(name,i18nText(source));
 document.querySelectorAll('.language-trigger').forEach(button=>{
  button.querySelector('.language-flag').className='language-flag flag-'+uiLanguage;
  button.setAttribute('aria-label',i18nText('Cambiar idioma')+': '+(uiLanguage==='ca'?'Català':'Español'));
 });
 document.querySelectorAll('.language-choice').forEach(button=>button.setAttribute('aria-checked',String(button.dataset.language===uiLanguage)));
}
function setUILanguage(language){
 if(!['es','ca'].includes(language))return;
 uiLanguage=language;
 try{localStorage.setItem('slotmuse-language',language);}catch{}
 applyStaticTranslations();
 if(typeof state!=='undefined'&&state)render();
}
const localizationReady=fetch('/ca.json').then(response=>{if(!response.ok)throw Error('Translation catalogue unavailable');return response.json();}).then(messages=>{
 catalanMessages=messages;
 const escape=key=>key.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 const keys=Object.keys(messages).filter(key=>messages[key]!==key).sort((a,b)=>b.length-a.length).map(escape);
 messagePattern=new RegExp('(?<![\\p{L}\\p{N}_])(?:'+keys.join('|')+')(?![\\p{L}\\p{N}_])','gu');
}).catch(()=>{uiLanguage='es';}).then(()=>{
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
 while(walker.nextNode()){const node=walker.currentNode;if(node.nodeValue.trim()&&!node.parentElement.closest('script,style'))staticText.set(node,node.nodeValue);}
 document.querySelectorAll('[aria-label],[placeholder],[title]').forEach(node=>{
  for(const attribute of ['aria-label','placeholder','title'])if(node.hasAttribute(attribute))staticAttributes.push([node,attribute,node.getAttribute(attribute)]);
 });
 applyStaticTranslations();
 const closeMenus=()=>document.querySelectorAll('.language-picker').forEach(picker=>{
  picker.querySelector('.language-menu').hidden=true;picker.querySelector('.language-trigger').setAttribute('aria-expanded','false');
 });
 document.querySelectorAll('.language-picker').forEach(picker=>{
  const trigger=picker.querySelector('.language-trigger'),menu=picker.querySelector('.language-menu');
  const open=()=>{closeMenus();menu.hidden=false;trigger.setAttribute('aria-expanded','true');};
  trigger.addEventListener('click',()=>{const show=menu.hidden;closeMenus();if(show){open();menu.querySelector('[aria-checked="true"]').focus();}});
  trigger.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();open();menu.querySelector('.language-choice').focus();}});
  picker.querySelectorAll('.language-choice').forEach(button=>button.addEventListener('click',()=>{setUILanguage(button.dataset.language);closeMenus();trigger.focus();}));
  picker.addEventListener('keydown',event=>{
   if(event.key==='Escape'){closeMenus();trigger.focus();}
   if(!menu.hidden&&['ArrowDown','ArrowUp'].includes(event.key)){
    event.preventDefault();const choices=[...menu.querySelectorAll('.language-choice')],index=choices.indexOf(document.activeElement);
    choices[(index+(event.key==='ArrowDown'?1:choices.length-1))%choices.length].focus();
   }
  });
 });
 document.addEventListener('click',event=>{if(!event.target.closest('.language-picker'))closeMenus();});
});
