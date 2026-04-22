(function(){"use strict";const I=`
  :host {
    all: initial;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    color: #1a1a1a;
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  .fb-trigger {
    position: fixed;
    bottom: 4px;
    right: 4px;
    font-size: 9px;
    color: #bbb;
    opacity: 0.25;
    text-decoration: none;
    cursor: default;
    z-index: 2147483646;
    transition: opacity 0.3s;
    background: none;
    border: none;
    font-family: inherit;
    letter-spacing: 0.5px;
  }
  .fb-trigger:hover {
    opacity: 0.6;
    cursor: pointer;
  }

  .fb-overlay {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: 340px;
    background: #fff;
    box-shadow: -4px 0 24px rgba(0,0,0,0.15);
    z-index: 2147483647;
    display: flex;
    flex-direction: column;
    transform: translateX(100%);
    transition: transform 0.25s ease;
    padding: 24px;
  }
  .fb-overlay.open {
    transform: translateX(0);
  }

  .fb-close {
    position: absolute;
    top: 12px;
    right: 12px;
    background: none;
    border: none;
    font-size: 20px;
    cursor: pointer;
    color: #666;
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
  }
  .fb-close:hover {
    background: #f0f0f0;
  }

  .fb-title {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 8px;
    color: #111;
  }

  .fb-desc {
    font-size: 13px;
    color: #666;
    line-height: 1.5;
    margin-bottom: 24px;
  }

  .fb-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 12px 24px;
    border: none;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s, transform 0.1s;
    font-family: inherit;
    width: 100%;
  }
  .fb-btn:active {
    transform: scale(0.98);
  }

  .fb-btn-primary {
    background: #2563eb;
    color: #fff;
  }
  .fb-btn-primary:hover {
    background: #1d4ed8;
  }

  .fb-btn-danger {
    background: #dc2626;
    color: #fff;
  }
  .fb-btn-danger:hover {
    background: #b91c1c;
  }

  .fb-recording-indicator {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
    padding: 12px 16px;
    background: #fef2f2;
    border-radius: 8px;
    border: 1px solid #fecaca;
  }

  .fb-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #dc2626;
    animation: fb-pulse 1s ease-in-out infinite;
  }

  @keyframes fb-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  .fb-timer {
    font-size: 16px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: #dc2626;
  }

  .fb-progress-wrap {
    margin-bottom: 16px;
  }

  .fb-progress-bar {
    height: 8px;
    background: #e5e7eb;
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 8px;
  }

  .fb-progress-fill {
    height: 100%;
    background: #2563eb;
    border-radius: 4px;
    transition: width 0.3s ease;
  }

  .fb-progress-text {
    font-size: 13px;
    color: #666;
    text-align: center;
  }

  .fb-success {
    text-align: center;
    padding: 32px 0;
  }

  .fb-success-icon {
    font-size: 48px;
    margin-bottom: 16px;
  }

  .fb-success-title {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 8px;
    color: #111;
  }

  .fb-success-desc {
    font-size: 13px;
    color: #666;
    line-height: 1.5;
  }

  .fb-error {
    padding: 12px 16px;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 8px;
    color: #dc2626;
    font-size: 13px;
    margin-bottom: 16px;
  }
`;let d=null,u=null,h=null,x=null,k=[],m=0,w=null,N=null;function B(){return{status:U(),error:null,blob:null,durationSeconds:m?(Date.now()-m)/1e3:0}}function U(){return d?d.state==="recording"?"recording":d.state==="inactive"&&k.length>0?"stopped":"idle":"idle"}function g(t){N&&N({...B(),...t})}function _(){const t=["video/webm;codecs=vp9,opus","video/webm;codecs=vp8,opus","video/webm"];for(const s of t)if(MediaRecorder.isTypeSupported(s))return s;return"video/webm"}function $(t,s){const c=new AudioContext,l=c.createMediaStreamDestination();for(const n of t)c.createMediaStreamSource(new MediaStream([n])).connect(l);for(const n of s)c.createMediaStreamSource(new MediaStream([n])).connect(l);return{stream:l.stream,ctx:c}}function L(t){N=t}async function P(){g({status:"requesting"});try{u=await navigator.mediaDevices.getDisplayMedia({video:!0,audio:!0,preferCurrentTab:!0}),h=await navigator.mediaDevices.getUserMedia({audio:!0});const t=u.getAudioTracks(),s=h.getAudioTracks();let c;if(t.length>0||s.length>0){const n=$(t,s);x=n.ctx,c=new MediaStream([...u.getVideoTracks(),...n.stream.getAudioTracks()])}else c=u;const l=_();k=[],d=new MediaRecorder(c,{mimeType:l}),d.ondataavailable=n=>{n.data.size>0&&k.push(n.data)},d.onstop=()=>{w&&(clearInterval(w),w=null);const n=new Blob(k,{type:l}),p=m?(Date.now()-m)/1e3:0;g({status:"stopped",blob:n,durationSeconds:p}),T()},d.onerror=()=>{g({status:"error",error:"Recording failed"}),T()},u.getVideoTracks()[0].onended=()=>{d&&d.state==="recording"&&d.stop()},d.start(1e3),m=Date.now(),w=setInterval(()=>{g({status:"recording"})},1e3),g({status:"recording"})}catch(t){const s=t instanceof Error?t.message:"Failed to start recording";g({status:"error",error:s}),T()}}function z(){d&&d.state==="recording"&&d.stop()}function T(){u&&(u.getTracks().forEach(t=>t.stop()),u=null),h&&(h.getTracks().forEach(t=>t.stop()),h=null),x&&(x.close(),x=null),d=null,m=0}function F(t){return new Promise((s,c)=>{const l=new FormData;l.append("video",t.blob,"feedback.webm"),l.append("site_id",t.siteId),l.append("page_url",window.location.href),l.append("user_agent",navigator.userAgent);const n=new XMLHttpRequest;n.upload.onprogress=p=>{p.lengthComputable&&t.onProgress&&t.onProgress(Math.round(p.loaded/p.total*100))},n.onload=()=>{n.status>=200&&n.status<300?s(JSON.parse(n.responseText)):c(new Error(`Upload failed: ${n.status} ${n.statusText}`))},n.onerror=()=>c(new Error("Upload failed: network error")),n.open("POST",`${t.apiBase}/api/feedback/submit`),n.send(l)})}function R(t){const s=document.createElement("div");s.id="feedback-widget-host",document.body.appendChild(s);const c=s.attachShadow({mode:"open"}),l=document.createElement("style");l.textContent=I,c.appendChild(l);let n="idle",p=null,y=0,C=0,v="";const S=document.createElement("button");S.className="fb-trigger",S.textContent="Feedback",c.appendChild(S);const a=document.createElement("div");a.className="fb-overlay",c.appendChild(a),L(e=>{e.status==="recording"?(y=e.durationSeconds,f("recording")):e.status==="stopped"&&e.blob?(p=e.blob,y=e.durationSeconds,Y()):e.status==="error"&&(v=e.error||"Recording failed",f("error"))});function f(e){n=e,M()}function W(e){const o=Math.floor(e/60),r=Math.floor(e%60);return`${o}:${r.toString().padStart(2,"0")}`}function M(){a.innerHTML="";const e=document.createElement("button");switch(e.className="fb-close",e.textContent="×",e.onclick=()=>{a.classList.remove("open"),n==="recording"&&z(),(n==="done"||n==="error")&&D()},a.appendChild(e),n){case"idle":case"ready":q();break;case"recording":O();break;case"uploading":X();break;case"done":H();break;case"error":V();break}}function q(){const e=document.createElement("h2");e.className="fb-title",e.textContent="Record Feedback",a.appendChild(e);const o=document.createElement("p");o.className="fb-desc",o.textContent="Thanks for using this feedback tool. If you clicked on this accidentally, feel free to close this panel or refresh the page. By submitting a recording here, it will be sent to the website support team at becomedistinct.com, and changes will be made to the website based on the feedback you provide.",a.appendChild(o);const r=document.createElement("p");r.className="fb-desc",r.textContent="Your browser will ask permission to capture your screen and microphone.",a.appendChild(r);const i=document.createElement("button");i.className="fb-btn fb-btn-primary",i.textContent="Start Recording",i.onclick=async()=>{i.disabled=!0,i.textContent="Requesting permissions...",await P()},a.appendChild(i)}function O(){const e=document.createElement("h2");e.className="fb-title",e.textContent="Recording...",a.appendChild(e);const o=document.createElement("div");o.className="fb-recording-indicator";const r=document.createElement("div");r.className="fb-dot";const i=document.createElement("span");i.className="fb-timer",i.textContent=W(y),o.appendChild(r),o.appendChild(i),a.appendChild(o);const b=document.createElement("p");b.className="fb-desc",b.textContent="Navigate the page and talk through your feedback. Click stop when you're done.",a.appendChild(b);const E=document.createElement("button");E.className="fb-btn fb-btn-danger",E.textContent="Stop & Submit",E.onclick=()=>z(),a.appendChild(E)}function X(){const e=document.createElement("h2");e.className="fb-title",e.textContent="Uploading...",a.appendChild(e);const o=document.createElement("div");o.className="fb-progress-wrap";const r=document.createElement("div");r.className="fb-progress-bar";const i=document.createElement("div");i.className="fb-progress-fill",i.style.width=`${C}%`,r.appendChild(i),o.appendChild(r);const b=document.createElement("div");b.className="fb-progress-text",b.textContent=`${C}%`,o.appendChild(b),a.appendChild(o)}function H(){const e=document.createElement("div");e.className="fb-success";const o=document.createElement("div");o.className="fb-success-icon",o.textContent="✅",e.appendChild(o);const r=document.createElement("h2");r.className="fb-success-title",r.textContent="Thank you!",e.appendChild(r);const i=document.createElement("p");i.className="fb-success-desc",i.textContent="Your feedback has been submitted. We appreciate you taking the time to help us improve.",e.appendChild(i),a.appendChild(e)}function V(){const e=document.createElement("h2");e.className="fb-title",e.textContent="Something went wrong",a.appendChild(e);const o=document.createElement("div");o.className="fb-error",o.textContent=v,a.appendChild(o);const r=document.createElement("button");r.className="fb-btn fb-btn-primary",r.textContent="Try Again",r.onclick=()=>{D(),f("ready")},a.appendChild(r)}async function Y(){if(p){f("uploading");try{await F({blob:p,siteId:t.siteId,apiBase:t.apiBase,onProgress:e=>{C=e,M()}}),f("done")}catch(e){v=e instanceof Error?e.message:"Upload failed",f("error")}}}function D(){p=null,y=0,C=0,v="",n="idle"}S.onclick=()=>{f("ready"),a.classList.add("open")},M()}function A(){if(window.__feedbackWidgetConfig){R(window.__feedbackWidgetConfig);return}const t=document.currentScript||document.querySelector('script[data-site][src*="feedback"]'),s=(t==null?void 0:t.getAttribute("data-site"))||"default",c=(t==null?void 0:t.getAttribute("data-api"))||"";R({siteId:s,apiBase:c})}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",A):A()})();
