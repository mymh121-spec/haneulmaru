import {streamBridge as bridge} from './app.js';
import {signaling,peer,sendData,streamProfiles,configureSender,streamDelta} from './rtc.js';
const box=document.createElement('section');box.style.cssText='padding:12px;border:1px solid #78debe;margin-bottom:12px';const imuPanel=document.querySelector('.imu-panel');if(imuPanel)imuPanel.after(box);else document.getElementById('panel').prepend(box);
box.innerHTML='<b>PC 서버 렌더러 · 전송 v2</b><p id="streamStatus">휴대폰 연결 대기</p><label>전송 화질 <select id="streamQuality"></select></label><p id="streamConfig"></p><p id="streamStats"></p><img id="streamQr" style="width:140px;background:white"><p><a id="streamLink" target="_blank" rel="noopener">휴대폰 접속 주소</a></p><p>장면 품질과 전송 화질은 별도입니다. 같은 핫스팟에서 이 탭을 화면에 열어두세요.</p>';
const $=id=>document.getElementById(id);
for(const [value,p] of Object.entries(streamProfiles)){const o=document.createElement('option');o.value=value;o.textContent=p.label;$('streamQuality').append(o);}
for(const key of ['ar','xr','sensor'])$(key).disabled=true;
let pc=null,id='',pending=[],media=null,sender=null,streamKey='sharp',transport=null,statsLast=null,configQueue=Promise.resolve();
function status(s){$('streamStatus').textContent=(pc?.connectionState==='connected'&&/timed out|timeout|fetch/i.test(s))?'영상 연결 유지 · 연결정보 서버 재시도 중':s;}
const signal=signaling('host',status);
async function info(){try{const d=await signal.api('info');const link=$('streamLink');link.href=link.textContent=d.url;$('streamQr').src='data:image/svg+xml;base64,'+d.qr;if(!d.url.startsWith('https'))setTimeout(info,3000);}catch(e){status(e.message);setTimeout(info,3000);}}info();
function configure(){const current=sender,key=streamKey,profile=streamProfiles[key];bridge.setStreamEdge(profile.edge);$('streamQuality').value=key;
 configQueue=configQueue.catch(()=>{}).then(async()=>{if(!current||sender!==current)return;try{const p=await configureSender(current,profile);if(sender!==current)return;const e=p.encodings[0];$('streamConfig').textContent=`설정 적용: 긴 변 ${profile.edge}px / 상한 ${(e.maxBitrate/1e6).toFixed(0)} Mbps / ${e.maxFramerate} FPS · 해상도 우선 (보장값 아님)`;}catch(e){$('streamConfig').textContent='전송 설정 적용 실패: '+e.message;}});return configQueue;}
$('streamQuality').value=streamKey;$('streamQuality').onchange=()=>{streamKey=$('streamQuality').value;configure();};
async function makeOffer(client){pc?.close();media?.getTracks().forEach(t=>t.stop());bridge.resetSession();id=client;pending=[];statsLast=null;transport=null;pc=peer(status,receive);const thisPeer=pc,thisId=id;pc.channel=pc.createDataChannel('control',{ordered:false,maxRetransmits:0});pc.channel.onmessage=e=>{try{receive(JSON.parse(e.data));}catch{}};
 bridge.setStreamEdge(streamProfiles[streamKey].edge);media=bridge.canvas.captureStream(30);const track=media.getVideoTracks()[0];track.contentHint='detail';sender=pc.addTrack(track,media);
 pc.onicecandidate=e=>{if(e.candidate)signal.send({type:'ice',session:thisId,candidate:e.candidate.toJSON()}).catch(e=>status(e.message));};
 await pc.setLocalDescription(await pc.createOffer());if(pc===thisPeer)await signal.send({type:'offer',session:id,sdp:pc.localDescription});}
function receive(d){if(d.type==='control'){bridge.control(d);if(Object.hasOwn(streamProfiles,d.streamKey)&&streamKey!==d.streamKey){streamKey=d.streamKey;configure();}}if(d.type==='ping')sendData(pc,{type:'pong',at:d.at});}
signal.poll(async m=>{if(m.type==='ready'){await makeOffer(m.session);return;}if(m.session!==id||!pc)return;if(m.type==='answer'){await pc.setRemoteDescription(m.sdp);for(const c of pending)await pc.addIceCandidate(c);pending=[];await configure();}if(m.type==='ice'){if(pc.remoteDescription)await pc.addIceCandidate(m.candidate);else pending.push(m.candidate);}});
setInterval(()=>sendData(pc,{type:'state',...bridge.state(),streamKey,transport}),500);
setInterval(()=>sendData(pc,{type:'coordinate-pose',pose:bridge.coordinatePose()}),50);
let collecting=false;
setInterval(async()=>{if(collecting||pc?.connectionState!=='connected')return;collecting=true;const current=pc;try{const reports=await current.getStats();if(pc!==current)return;reports.forEach(s=>{if(s.type==='outbound-rtp'&&s.kind==='video'&&!s.isRemote){transport=streamDelta(s,statsLast);statsLast=s;const t=transport;const reason={none:'제한 없음',cpu:'CPU 제한',bandwidth:'대역폭 제한',other:'기타 제한',unknown:'미제공'}[t.reason]||t.reason;$('streamStats').textContent=`실측 ${t.width}×${t.height} / ${t.fps.toFixed(1)} FPS / ${t.mbps.toFixed(1)} Mbps · 인코딩 ${t.encodeMs.toFixed(1)}ms · ${reason} · ${t.encoder}`;}});}catch(e){$('streamStats').textContent='통계 수집 실패: '+e.message;}finally{collecting=false;}},1000);
addEventListener('beforeunload',()=>{signal.stop();pc?.close();media?.getTracks().forEach(t=>t.stop());});
