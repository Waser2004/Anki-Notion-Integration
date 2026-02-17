function decodeList(value){
if(!value){return [];}
var normalized=String(value).trim().replace(/-/g,'+').replace(/_/g,'/');
if(!normalized){return [];}
while(normalized.length%4){normalized+='=';}
try{var decoded=atob(normalized);var parsed=JSON.parse(decoded);
if(Array.isArray(parsed)){return parsed.filter(function(x){return typeof x==='string';});}}
catch(_err){return [];}
return [];
}
function pad2(value){return value<10?'0'+String(value):String(value);}
function utcDateKey(){
var now=new Date();
return String(now.getUTCFullYear())+'-'+pad2(now.getUTCMonth()+1)+'-'+pad2(now.getUTCDate());
}
function stableHash32(input){
var hash=2166136261;
for(var idx=0;idx<input.length;idx++){
hash^=input.charCodeAt(idx);
hash+=(hash<<1)+(hash<<4)+(hash<<7)+(hash<<8)+(hash<<24);
}
return hash>>>0;
}
function deterministicVariantIndex(blockId,direction,length){
if(!(length>0)){return 0;}
var seed=String(blockId||'')+'|'+String(direction||'forward')+'|'+utcDateKey();
return stableHash32(seed)%length;
}
function tryAutoplayAudio(source,mountNode){
var audioSource=String(source||'').trim();
if(!audioSource){return;}
function showFallback(){
var host=mountNode&&mountNode.appendChild?mountNode:null;
if(!host){return;}
if(host.querySelector&&host.querySelector('.noteck-ai-audio-fallback')){return;}
var wrap=document.createElement('div');
wrap.className='noteck-ai-audio-fallback';
var btn=document.createElement('button');
btn.type='button';
btn.className='noteck-ai-audio-play-button';
btn.textContent='Play audio';
btn.addEventListener('click',function(){
try{var manualAudio=new Audio(audioSource);manualAudio.play();}catch(_manualErr){}
});
wrap.appendChild(btn);
host.appendChild(wrap);
}
try{
var autoAudio=new Audio(audioSource);
var playPromise=autoAudio.play();
if(playPromise&&typeof playPromise.catch==='function'){
playPromise.catch(function(){showFallback();});
}
}catch(_audioErr){showFallback();}
}
