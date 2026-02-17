(function(){
function byId(id){return document.getElementById(id);}
function clearChildren(el){if(!el){return;}while(el.firstChild){el.removeChild(el.firstChild);}}
window.NoteckAiEvalRenderLoading=function(){
var status=byId('noteck-ai-eval-status');
var fill=byId('noteck-ai-eval-progress-fill');
var verdict=byId('noteck-ai-eval-verdict');
var feedback=byId('noteck-ai-eval-feedback');
var missing=byId('noteck-ai-eval-missing');
if(status){status.textContent='Evaluating answer...';}
if(fill){fill.style.width='0%';}
if(verdict){verdict.textContent='';}
if(feedback){feedback.textContent='';}
clearChildren(missing);
};
window.NoteckAiEvalRenderError=function(message){
var status=byId('noteck-ai-eval-status');
if(status){status.textContent=String(message||'AI evaluation failed.');}
};
window.NoteckAiEvalRender=function(payload){
if(!payload||typeof payload!=='object'){window.NoteckAiEvalRenderError('Invalid AI response.');return;}
var score=Number(payload.score||0);if(!Number.isFinite(score)){score=0;}score=Math.max(0,Math.min(1,score));
var verdictText=String(payload.verdict||'incorrect');
var feedbackText=String(payload.feedback||'');
var status=byId('noteck-ai-eval-status');
var fill=byId('noteck-ai-eval-progress-fill');
var verdict=byId('noteck-ai-eval-verdict');
var feedback=byId('noteck-ai-eval-feedback');
var missing=byId('noteck-ai-eval-missing');
if(status){status.textContent='AI evaluation completed.';}
if(fill){fill.style.width=String(Math.round(score*100))+'%';}
if(verdict){verdict.textContent='Verdict: '+verdictText+' ('+String(Math.round(score*100))+'%)';}
if(feedback){feedback.textContent=feedbackText;}
clearChildren(missing);
if(missing&&Array.isArray(payload.missing_points)){
payload.missing_points.forEach(function(item){
if(typeof item!=='string'||!item.trim()){return;}
var li=document.createElement('li');li.textContent=item;missing.appendChild(li);
});
}
};
window.NoteckAiEvalRenderLoading();
})();
