import * as T from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
import {buildWorld,profiles} from './world.js';
import {createCoordinateRecorder} from './coordinate-ui.js';
const $=id=>document.getElementById(id),say=s=>$('message').textContent=s;
const mobile=matchMedia('(pointer:coarse)').matches;
const renderer=new T.WebGLRenderer({canvas:$('view'),antialias:true,alpha:true,powerPreference:'high-performance'});
renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;renderer.shadowMap.type=T.PCFShadowMap;
const gl=renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');const gpu=ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);
const software=/swiftshader|software|llvmpipe|basic render/i.test(gpu);$('gpu').textContent=`실제 WebGL 장치: ${gpu}. ${software?'소프트웨어 렌더러 — RTX 사용 확인 안 됨.':'브라우저 그래픽 장치 · CUDA 경로 아님.'}`;
const scene=new T.Scene();scene.background=new T.Color(0x9bbfcf);scene.fog=new T.FogExp2(0xa5c6d0,.00048);
const camera=new T.PerspectiveCamera(55,innerWidth/innerHeight,.1,4500);camera.position.set(620,450,660);
const ortho=new T.OrthographicCamera(-700,700,700,-700,.1,4000);ortho.position.set(0,1200,0);ortho.up.set(0,0,-1);ortho.lookAt(0,0,0);
let active=camera,controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,40,0);controls.enableDamping=true;controls.minDistance=12;controls.maxDistance=1900;controls.maxPolarAngle=Math.PI*.49;controls.update();
scene.add(new T.HemisphereLight(0xd8efff,0x6a7259,2.1));const sun=new T.DirectionalLight(0xffe1ac,3);sun.position.set(-380,620,240);sun.castShadow=true;sun.shadow.camera.left=sun.shadow.camera.bottom=-650;sun.shadow.camera.right=sun.shadow.camera.top=650;sun.shadow.camera.near=1;sun.shadow.camera.far=1800;sun.shadow.bias=-.0003;sun.shadow.normalBias=1.2;scene.add(sun);
let world,quality,mode='virtual',paused=false,clock=0,last=0,stream=null,xrSession=null,hitSource=null,xrSpace=null,placed=false,top=false,sensor=false,sensorOrigin=null,raw=null;
let measurements=[],benchmark=null,benchSamples=[],frameSamples=[],lastStat=0,installPrompt=null,remoteAspect=null,remoteAR=false,remoteSeq=-1,streamEdge=1920,imuEnabled=false;
let coordinateRecorder=null,coordinateImuPose=null,coordinateXRPose=null,coordinateXRStarted=false;
function resize(){if(renderer.xr.isPresenting)return;const p=profiles[quality||'balanced'];if(remoteAspect){const long=streamEdge;renderer.setPixelRatio(1);renderer.setSize(remoteAspect>1?long:Math.round(long*remoteAspect),remoteAspect>1?Math.round(long/remoteAspect):long,false);camera.aspect=remoteAspect;}else{renderer.setPixelRatio(Math.min(devicePixelRatio,p.dpr));renderer.setSize(innerWidth,innerHeight,false);camera.aspect=innerWidth/innerHeight;}camera.updateProjectionMatrix();ortho.left=-700*camera.aspect;ortho.right=700*camera.aspect;ortho.updateProjectionMatrix();}
function setQuality(value){if(xrSession){say('공간 AR을 종료한 뒤 품질을 변경하세요.');$('quality').value=quality;return;}quality=value;const old=world;world=buildWorld(value);scene.add(world.root);if(old){scene.remove(old.root);old.dispose();}renderer.shadowMap.enabled=profiles[value].shadow>0;sun.shadow.mapSize.setScalar(profiles[value].shadow||512);if(sun.shadow.map){sun.shadow.map.dispose();sun.shadow.map=null;}resize();$('quality').value=value;say(`${value} 품질 · ${((profiles[value].segments+1)**2).toLocaleString()} 지형 정점. 실제 FPS를 확인하세요.`);}
setQuality(software?'low':mobile?'balanced':'high');
addEventListener('resize',resize);screen.orientation?.addEventListener('change',resize);visualViewport?.addEventListener('resize',resize);
function stopCamera(){coordinateRecorder?.autoStop();stream?.getTracks().forEach(t=>t.stop());stream=null;$('camera').srcObject=null;}
function normalBackground(){scene.background=mode==='virtual'?new T.Color(0x9bbfcf):null;scene.fog=mode==='virtual'?new T.FogExp2(0xa5c6d0,.00048):null;document.body.classList.toggle('ar',mode==='ar');$('mode').textContent=mode==='virtual'?'가상환경':mode==='ar'?'카메라 AR · 터치/방향 기반':'공간 AR · 바닥에 놓기';$('virtual').classList.toggle('active',mode==='virtual');$('ar').classList.toggle('active',mode==='ar');}
async function virtual(){if(xrSession)await xrSession.end();stopCamera();mode='virtual';normalBackground();say('가상환경 · 드래그 회전 / 핀치 확대');}
$('virtual').onclick=virtual;
$('ar').onclick=async()=>{try{if(xrSession)await xrSession.end();if(!isSecureContext)throw Error('카메라 AR은 HTTPS 접속이 필요합니다.');stopCamera();stream=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}}});$('camera').srcObject=stream;await $('camera').play();mode='ar';normalBackground();coordinateRecorder?.autoStart();say('카메라 위 합성 화면입니다. 실제 공간 고정은 공간 AR을 사용하세요.');}catch(e){stopCamera();mode='virtual';normalBackground();say('카메라 시작 실패: '+e.message);}};
const reticle=new T.Mesh(new T.RingGeometry(.08,.105,40).rotateX(-Math.PI/2),new T.MeshBasicMaterial({color:0x72ffd0}));reticle.matrixAutoUpdate=false;reticle.visible=false;scene.add(reticle);
$('xr').onclick=async()=>{try{
 if(imuEnabled)throw Error('IMU 중지 후 공간 AR을 시작하세요.');
 if(xrSession){await xrSession.end();return;}
 if(!navigator.xr||!await navigator.xr.isSessionSupported('immersive-ar'))throw Error('이 브라우저/기기는 공간 AR을 지원하지 않습니다. 카메라 AR을 사용하세요.');
 stopCamera();sensor=false;sensorOrigin=null;top=false;active=camera;controls.enabled=false;renderer.xr.enabled=true;renderer.xr.setReferenceSpaceType('local');
 const session=await navigator.xr.requestSession('immersive-ar',{requiredFeatures:['hit-test'],optionalFeatures:['dom-overlay'],domOverlay:{root:$('ui')}});xrSession=session;coordinateXRStarted=false;coordinateXRPose=null;
 session.addEventListener('end',()=>{coordinateRecorder?.autoStop();coordinateXRPose=null;hitSource?.cancel();hitSource=null;xrSpace=null;xrSession=null;reticle.visible=false;world.root.visible=true;world.root.position.set(0,0,0);world.root.quaternion.identity();world.root.scale.setScalar(1);renderer.xr.enabled=false;controls.enabled=true;document.body.classList.remove('xr');$('arScaleLabel').hidden=true;$('xr').textContent='공간 AR';$('quality').disabled=false;mode='virtual';normalBackground();resetView();resize();});
 await renderer.xr.setSession(session);xrSpace=await session.requestReferenceSpace('viewer');hitSource=await session.requestHitTestSource({space:xrSpace});placed=false;world.root.visible=false;mode='xr';normalBackground();document.body.classList.add('xr');$('arScaleLabel').hidden=false;$('xr').textContent='AR 종료';$('quality').disabled=true;
 session.addEventListener('select',()=>{if(reticle.visible){reticle.matrix.decompose(world.root.position,world.root.quaternion,new T.Vector3());world.root.scale.setScalar(+$('arScale').value/1200);world.root.visible=true;placed=true;say('배치 완료 · 주변으로 이동해 보세요. 초기 지점으로 다시 배치할 수 있습니다.');}});
 say('휴대폰으로 바닥을 비추세요. 원형 표시가 나타나면 화면을 탭해 지형을 놓으세요.');
 }catch(e){if(xrSession)await xrSession.end();renderer.xr.enabled=false;controls.enabled=true;say(e.message);}};
$('ui').addEventListener('beforexrselect',e=>{if(e.target.closest('button,input,select,aside'))e.preventDefault();});
$('arScale').oninput=()=>{if(xrSession)world.root.scale.setScalar(+$('arScale').value/1200);};
function resetView(){coordinateRecorder?.autoStop();sensorOrigin=null;sensor=false;$('sensor').textContent='폰 회전 시점';if(xrSession){placed=false;world.root.visible=false;return;}top=false;active=camera;controls.enabled=!imuEnabled;camera.position.set(620,450,660);controls.target.set(0,40,0);controls.update();$('top').textContent='2D 지도';}
$('reset').onclick=resetView;
$('top').onclick=()=>{if(xrSession)return;top=!top;active=top?ortho:camera;controls.enabled=!top&&!sensor&&!imuEnabled;$('top').textContent=top?'3D 지형':'2D 지도';};
$('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'재생':'일시정지';};
$('count').oninput=()=>$('countText').textContent=$('count').value;
$('quality').onchange=()=>setQuality($('quality').value);
$('sun').oninput=()=>{const deg=+$('sun').value,a=deg*Math.PI/180;sun.position.set(-Math.cos(a)*700,Math.sin(a)*800,240);sun.color.setHSL(.09,.65-(deg/160),.85);sun.intensity=1.5+deg/35;$('sunText').textContent=deg<25?'해질녘':deg<45?'오후':'낮';};
const deviceQ=new T.Quaternion(),euler=new T.Euler(),fix=new T.Quaternion().setFromAxisAngle(new T.Vector3(1,0,0),-Math.PI/2),screenQ=new T.Quaternion();let sensorBase=new T.Quaternion();
addEventListener('deviceorientation',e=>{if(![e.alpha,e.beta,e.gamma].every(Number.isFinite))return;euler.set(e.beta*Math.PI/180,e.alpha*Math.PI/180,-e.gamma*Math.PI/180,'YXZ');deviceQ.setFromEuler(euler).multiply(fix).multiply(screenQ.setFromAxisAngle(new T.Vector3(0,0,1),-(screen.orientation?.angle||0)*Math.PI/180));raw=deviceQ.clone();});
$('sensor').onclick=async()=>{try{if(imuEnabled)throw Error('IMU 중지 후 폰 회전 시점을 사용하세요.');if(sensor){sensor=false;controls.enabled=!top;controls.target.copy(camera.position).add(new T.Vector3(0,0,-1).applyQuaternion(camera.quaternion).multiplyScalar(300));$('sensor').textContent='폰 회전 시점';return;}if(typeof DeviceOrientationEvent==='undefined')throw Error('회전 센서 API가 없습니다.');if(DeviceOrientationEvent.requestPermission&&await DeviceOrientationEvent.requestPermission()!=='granted')throw Error('동작 센서 권한이 필요합니다.');sensor=true;top=false;active=camera;controls.enabled=false;sensorOrigin=null;sensorBase.copy(camera.quaternion);$('sensor').textContent='터치 시점';say('회전 센서 대기 · 기기를 움직이세요. 센서 미지원 시 터치 시점으로 전환하세요.');}catch(e){say(e.message);}};
$('hide').onclick=async()=>{document.body.classList.add('immersed');try{await document.documentElement.requestFullscreen();}catch{}};$('show').onclick=()=>{document.body.classList.remove('immersed');if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});};
addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;});$('install').onclick=async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;}else say('Android Chrome 메뉴 → 앱 설치 또는 홈 화면에 추가. HTTPS 접속이 필요합니다.');};
if('serviceWorker'in navigator)navigator.serviceWorker.register('./sw.js').then(()=>navigator.serviceWorker.ready).then(async()=>{const cache=await caches.open('haneulmaru-pro-3');const entries=await cache.keys();$('offline').textContent=`오프라인 캐시: ${entries.length}개 파일 준비. 첫 접속 후 브라우저 캐시 유지 필요.`;}).catch(e=>{$('offline').textContent='오프라인 캐시 등록 실패: '+e.message;});
$('bench').onclick=()=>{benchSamples=[];benchmark=performance.now();say('10초간 FPS 측정 중 — 화면을 유지하세요.');};
function report(samples){const sorted=[...samples].sort((a,b)=>a-b),avg=samples.reduce((a,b)=>a+b,0)/samples.length;return {at:new Date().toISOString(),gpu,software,quality,count:+$('count').value,mode,width:renderer.domElement.width,height:renderer.domElement.height,frames:samples.length,meanFps:1000/avg,p95FrameMs:sorted[Math.floor(sorted.length*.95)],phoneDimensionsCm:[16.5,7.5,1],note:'Browser frame interval; not network latency or physical-phone validation.'};}
$('export').onclick=()=>{if(!measurements.length){say('먼저 10초 성능 측정을 실행하세요.');return;}const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(measurements,null,2)],{type:'application/json'}));a.href=url;a.download='haneulmaru-pro-benchmark.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const mapCtx=$('map').getContext('2d'),mapBase=document.createElement('canvas');mapBase.width=mapBase.height=180;
// Mini-map is a top projection of the same synthetic world, with matching live positions.
const mc=mapBase.getContext('2d');mc.fillStyle='#142d38';mc.fillRect(0,0,180,180);mc.strokeStyle='#31515b';for(let i=0;i<180;i+=30){mc.beginPath();mc.moveTo(i,0);mc.lineTo(i,180);mc.moveTo(0,i);mc.lineTo(180,i);mc.stroke();}mc.fillStyle='#b8deda';mc.font='11px sans-serif';mc.fillText('N ↑',7,14);
let mapAt=0;
renderer.setAnimationLoop((now,frame)=>{
 const dt=last?Math.min((now-last)/1000,.1):0;const ms=last?now-last:0;last=now;if(!paused)clock+=dt*+$('speed').value;
 if(sensor&&raw&&!xrSession){if(!sensorOrigin)sensorOrigin=raw.clone().invert();camera.quaternion.copy(sensorBase).multiply(sensorOrigin.clone().multiply(raw));}else if(controls.enabled)controls.update();
 if(frame&&xrSession){const viewer=frame.getViewerPose(renderer.xr.getReferenceSpace());if(viewer){const q=viewer.transform.orientation;coordinateXRPose={quaternion:[q.x,q.y,q.z,q.w],timestamp_ms:Date.now(),valid:true,source:'WebXR_viewer_tracking',quality:'XR_tracking_pose'};if(!coordinateXRStarted){coordinateRecorder?.autoStart();coordinateXRStarted=true;}}else if(coordinateXRPose)coordinateXRPose.valid=false;}
 if(frame&&hitSource&&!placed){const hits=frame.getHitTestResults(hitSource);reticle.visible=hits.length>0;if(hits.length){const pose=hits[0].getPose(renderer.xr.getReferenceSpace());if(pose)reticle.matrix.fromArray(pose.transform.matrix);}}else reticle.visible=false;
 world.update(clock,+$('count').value);renderer.render(scene,active);
 if(ms>0&&!document.hidden){frameSamples.push(ms);if(benchmark)benchSamples.push(ms);}
 if(now-lastStat>1000){if(frameSamples.length){const avg=frameSamples.reduce((a,b)=>a+b,0)/frameSamples.length;$('fps').textContent=(1000/avg).toFixed(1)+' FPS';$('stats').textContent=`${quality} · ${renderer.info.render.triangles.toLocaleString()} triangles · ${renderer.info.render.calls} calls\n${renderer.domElement.width} × ${renderer.domElement.height} · ${$('count').value} SIM`;frameSamples=[];}lastStat=now;}
 if(benchmark&&performance.now()-benchmark>=10000){if(benchSamples.length){const r=report(benchSamples);measurements.push(r);say(`측정 완료: 평균 ${r.meanFps.toFixed(1)} FPS · P95 ${r.p95FrameMs.toFixed(1)} ms. JSON 저장 가능.`);}benchmark=null;}
 if(now-mapAt>100){mapAt=now;mapCtx.clearRect(0,0,180,180);mapCtx.drawImage(mapBase,0,0);world.positions.forEach((p,i)=>{mapCtx.fillStyle=['#ffd06a','#69ebff','#ff79a5'][i%3];mapCtx.beginPath();mapCtx.arc(90+p.x*.15,90+p.z*.15,i===0?3:1.7,0,Math.PI*2);mapCtx.fill();});}
});
addEventListener('visibilitychange',()=>{last=0;if(document.hidden&&benchmark){benchmark=null;say('측정 취소: 화면이 숨겨졌습니다. 다시 측정하세요.');}});
$('view').addEventListener('webglcontextlost',e=>{e.preventDefault();say('GPU 컨텍스트를 잃었습니다. eGPU 연결을 확인하고 새로고침하세요.');renderer.setAnimationLoop(null);});
async function connection(){try{const r=await fetch('./info',{cache:'no-store'}),data=await r.json();if(data.url){$('connect').textContent='HTTPS 주소 (현재 서버 실행 동안 유효)';$('phoneLink').href=data.url;$('phoneLink').textContent=data.url;$('qr').src='./qr';$('qr').hidden=false;return;}}catch{}setTimeout(connection,4000);}if(location.pathname!=="/host")connection();
normalBackground();
function captureCoordinatePose(){
 if(xrSession)return coordinateXRPose?{...coordinateXRPose,valid:coordinateXRPose.valid&&Date.now()-coordinateXRPose.timestamp_ms<=350}:{quaternion:[0,0,0,1],source:'WebXR_viewer_tracking',valid:false};
 if(imuEnabled)return coordinateImuPose?{...coordinateImuPose,valid:coordinateImuPose.valid&&Date.now()-coordinateImuPose.timestamp_ms<=350}:{quaternion:[0,0,0,1],valid:false,source:'NU40_trained_axis_gyro_integration'};
 if(mode==='ar'&&raw)return {quaternion:raw.toArray(),timestamp_ms:Date.now(),source:'phone_orientation',quality:'device_orientation_estimate'};
 return {quaternion:camera.quaternion.toArray(),timestamp_ms:Date.now(),source:'renderer_view_assumed',quality:'virtual_camera_pose_assumption'};
}
export const streamBridge={canvas:renderer.domElement,setStreamEdge(edge){if([960,1280,1920,2560].includes(edge)){streamEdge=edge;if(remoteAspect)resize();}},setImuEnabled(value){coordinateRecorder?.autoStop();coordinateImuPose=null;imuEnabled=!!value;if(imuEnabled){sensor=false;sensorOrigin=null;top=false;active=camera;$('top').textContent='2D 지도';$('sensor').textContent='폰 회전 시점';}else{controls.target.copy(camera.position).add(new T.Vector3(0,0,-1).applyQuaternion(camera.quaternion).multiplyScalar(300));}controls.enabled=!imuEnabled&&!top;},
 resetView,setCoordinateImuPose(pose){coordinateImuPose=pose;},coordinatePose:captureCoordinatePose,
 startCoordinateRecording:pose=>coordinateRecorder.start(pose),stopCoordinateRecording:()=>coordinateRecorder.stop(),
 recordDroneDetections:packet=>coordinateRecorder.recordFrame(packet),coordinateState:()=>coordinateRecorder.snapshot(),
 imuRotate(yaw,pitch){if(!imuEnabled||top||xrSession||![yaw,pitch].every(Number.isFinite))return;const e=new T.Euler().setFromQuaternion(camera.quaternion,'YXZ');e.y+=yaw;e.x=T.MathUtils.clamp(e.x+pitch,-Math.PI*.47,Math.PI*.47);e.z=0;camera.quaternion.setFromEuler(e);},
 state:()=>({imuEnabled,gpu,count:+$('count').value,paused,top,quality,coordinatePose:captureCoordinatePose()}),control(d){
 if(!Number.isSafeInteger(d.seq)||d.seq<=remoteSeq)return;remoteSeq=d.seq;
 const finite=(v,lo,hi)=>Number.isFinite(v)&&v>=lo&&v<=hi;
 if(finite(d.aspect,.2,5)){remoteAspect=d.aspect;resize();}
 if(typeof d.ar==='boolean'){remoteAR=d.ar;scene.background=new T.Color(remoteAR?0x00ff00:0x9bbfcf);scene.fog=remoteAR?null:new T.FogExp2(0xa5c6d0,.00048);}
 if(Number.isInteger(d.count)&&d.count>=1&&d.count<=200){$('count').value=d.count;$('countText').textContent=d.count;}
 if(typeof d.paused==='boolean'){paused=d.paused;$('pause').textContent=paused?'재생':'일시정지';}
 if(typeof d.top==='boolean'){top=d.top;active=top?ortho:camera;controls.enabled=!top&&!imuEnabled;$('top').textContent=top?'3D 지형':'2D 지도';}
 if(d.reset){resetView();}
 if(typeof d.ar==='boolean'){if(d.ar)coordinateRecorder?.autoStart();else coordinateRecorder?.autoStop();}
 if(!imuEnabled&&finite(d.dx,-2,2)&&finite(d.dy,-2,2)){controls.enabled=false;const sph=new T.Spherical().setFromVector3(camera.position.clone().sub(controls.target));sph.theta-=d.dx*Math.PI*2;sph.phi=T.MathUtils.clamp(sph.phi+d.dy*Math.PI,.05,Math.PI*.49);camera.position.copy(controls.target).add(new T.Vector3().setFromSpherical(sph));camera.lookAt(controls.target);}
 if(!imuEnabled&&finite(d.zoom,.2,5)){camera.position.sub(controls.target).multiplyScalar(d.zoom).clampLength(12,1900).add(controls.target);camera.lookAt(controls.target);}
 if(!imuEnabled&&Array.isArray(d.q)&&d.q.length===4&&d.q.every(x=>finite(x,-1,1))){controls.enabled=false;const b=new T.Quaternion().setFromRotationMatrix(new T.Matrix4().lookAt(camera.position,controls.target,camera.up));camera.quaternion.copy(b).multiply(new T.Quaternion().fromArray(d.q).normalize());}
 },resetSession(){remoteSeq=-1;}};
coordinateRecorder=createCoordinateRecorder({container:$('panel'),video:$('camera'),getPose:captureCoordinatePose,isCameraActive:()=>mode==='ar'&&!!stream});
if(location.pathname==='/host'){renderer.domElement.style.objectFit='contain';import('./imu-input.js').catch(e=>say('IMU 시작 실패: '+e.message));import('./host-stream.js').catch(e=>say('스트리밍 시작 실패: '+e.message));}
const shot=document.createElement('button');shot.textContent='장면 PNG 저장';$('export').after(shot);shot.onclick=()=>{if(xrSession){say('장면 저장은 공간 AR을 종료한 뒤 가능합니다.');return;}renderer.render(scene,active);renderer.domElement.toBlob(blob=>{if(!blob)return;const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download='haneulmaru-pro-scene.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);},'image/png');};
