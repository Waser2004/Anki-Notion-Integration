(function(){
__SHARED_AI_HELPERS_JS__
var meta=document.querySelector('.noteck-ai-meta');
var questionNode=document.querySelector('.noteck-ai-question');
if(!meta||!questionNode){if(window.noteckIsBack===true){window.noteckIsBack=false;}return;}
var blockId=String(meta.getAttribute('data-block-id')||'');
var direction=String(meta.getAttribute('data-direction')||'forward');
var variants=decodeList(meta.getAttribute('data-variants'));
var audioFiles=decodeList(meta.getAttribute('data-audio'));
var isBack=window.noteckIsBack===true;
if(!variants.length){if(isBack){window.noteckIsBack=false;}return;}
var idx=deterministicVariantIndex(blockId,direction,variants.length);
questionNode.textContent=variants[idx];
if(isBack){window.noteckIsBack=false;return;}
var audioSrc=idx<audioFiles.length?audioFiles[idx]:'';
tryAutoplayAudio(audioSrc,questionNode.parentElement||questionNode);
})();
