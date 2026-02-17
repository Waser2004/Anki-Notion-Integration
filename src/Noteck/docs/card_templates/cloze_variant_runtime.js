(function(){
__SHARED_AI_HELPERS_JS__
function renderClozeVariant(variant,isBack){
var input=String(variant||'');
var regex=/\{\{c\d+::(.*?)(?:::(.*?))?\}\}/g;
var htmlParts=[];
var cursor=0;
for(;;){
var match=regex.exec(input);
if(!match){break;}
htmlParts.push(withLineBreaks(escapeHtml(input.slice(cursor,match.index))));
var answer=match[1]||'';
var hint=match[2]||'';
var frontLabel='[...]';
if(!isBack&&hint){frontLabel='['+hint+']';}
var token=isBack?escapeHtml(answer):escapeHtml(frontLabel);
htmlParts.push('<span class="cloze">'+token+'</span>');
cursor=match.index+match[0].length;
}
htmlParts.push(withLineBreaks(escapeHtml(input.slice(cursor))));
return htmlParts.join('');
}
var meta=document.querySelector('.noteck-ai-meta');
var questionNode=document.querySelector('.noteck-ai-cloze-text');
if(!meta||!questionNode){if(window.noteckIsBack===true){window.noteckIsBack=false;}return;}
var blockId=String(meta.getAttribute('data-block-id')||'');
var direction=String(meta.getAttribute('data-direction')||'__DEFAULT_DIRECTION__');
var variants=decodeList(meta.getAttribute('data-variants'));
var audioFiles=decodeList(meta.getAttribute('data-audio'));
var isBack=window.noteckIsBack===true;
if(!variants.length){if(isBack){window.noteckIsBack=false;}return;}
var idx=deterministicVariantIndex(blockId,direction,variants.length);
questionNode.innerHTML=renderClozeVariant(variants[idx],isBack);
typesetMath(questionNode);
if(isBack){window.noteckIsBack=false;return;}
var audioSrc=idx<audioFiles.length?audioFiles[idx]:'';
tryAutoplayAudio(audioSrc,questionNode.parentElement||questionNode);
})();
