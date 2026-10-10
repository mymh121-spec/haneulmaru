import {NorthCoordinateSession} from './drone-coordinates.js';

// A detector can call recordFrame directly, even when no AR marker is drawn.
export function createCoordinateRecorder({container,video,getPose,isCameraActive}) {
  const session=new NorthCoordinateSession();
  const panel=document.createElement('section');panel.className='imu-panel';
  panel.innerHTML='<b>드론 시스템 좌표</b><p role="status">촬영 시작 방향을 북쪽으로 잡습니다.</p><div class="modes"><button data-action="start">좌표 기록 시작</button><button data-action="stop">중지</button><button data-action="pick">드론 위치 지정</button><button data-action="export">좌표 JSON 저장</button></div><p class="muted">동쪽 · 북쪽 · 위쪽 / 거리 미정<br>55° 임시 화각으로 방향 추정</p>';
  container.append(panel);
  const status=panel.querySelector('[role=status]'),pick=panel.querySelector('[data-action=pick]');
  let armed=false,manualId=0;
  function unarm(){armed=false;pick.textContent='드론 위치 지정';}
  function start(initialPose){unarm();const id=session.start(initialPose||getPose());manualId=0;status.textContent='좌표 기록 중 · 시작 방향 = 북쪽 0° · 드론 입력 대기';return id;}
  function stop(){unarm();session.stop();status.textContent='좌표 기록 중지 · JSON으로 저장할 수 있습니다.';}
  function recordFrame(packet) {
    // Delayed detector results must carry the pose captured with the frame.
    if(!packet.pose&&Number.isFinite(packet.captured_at_ms)&&Math.abs(Date.now()-packet.captured_at_ms)>350)throw Error('지연된 탐지 결과에는 촬영 시점의 pose를 함께 보내주세요.');
    const data=session.record(packet,packet.pose||getPose());
    const count=data.observations.length;
    status.textContent=`좌표 기록 중 · 현재 드론 ${count}개 · ${session.frames.length}프레임 저장`;
    dispatchEvent(new CustomEvent('haneulmaru:drone-coordinates',{detail:data}));
    return data;
  }
  function markPixel(u,v) {
    const width=video.videoWidth,height=video.videoHeight;
    if(!isCameraActive()||!width||!height)throw Error('카메라를 켠 뒤 드론 위치를 지정하세요.');
    return recordFrame({captured_at_ms:Date.now(),image_size_px:[width,height],tracks:[{id:'manual-'+ ++manualId,code:'UNKNOWN',xyxy:[u,v,u,v]}]});
  }
  function exportJSON(){
    const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(session.snapshot(),null,2)],{type:'application/json'}));
    a.href=url;a.download='haneulmaru-drone-coordinates.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const safe=fn=>{try{return fn();}catch(e){status.textContent=e.message;}};
  panel.querySelector('[data-action=start]').onclick=()=>safe(start);
  panel.querySelector('[data-action=stop]').onclick=stop;
  panel.querySelector('[data-action=export]').onclick=()=>safe(exportJSON);
  pick.onclick=()=>safe(()=>{if(!session.active)start();if(!isCameraActive())throw Error('카메라를 켠 뒤 위치를 지정하세요.');armed=!armed;pick.textContent=armed?'화면의 드론을 누르세요':'드론 위치 지정';});
  document.addEventListener('pointerdown',e=>{
    if(!armed||e.target.closest('button,input,select,aside,section'))return;
    e.preventDefault();e.stopImmediatePropagation();
    safe(()=>{
      const rect=video.getBoundingClientRect(),width=video.videoWidth,height=video.videoHeight;
      const scale=Math.max(rect.width/width,rect.height/height); // video object-fit: cover
      const u=(e.clientX-rect.left+(width*scale-rect.width)/2)/scale;
      const v=(e.clientY-rect.top+(height*scale-rect.height)/2)/scale;
      markPixel(Math.max(0,Math.min(width,u)),Math.max(0,Math.min(height,v)));
    });unarm();
  },true);
  addEventListener('haneulmaru:drone-detections',event=>{if(session.active)safe(()=>recordFrame(event.detail));});
  const api={start,stop,recordFrame,markPixel,exportJSON,capturePose:()=>structuredClone(getPose()),latest:()=>structuredClone(session.latest),snapshot:()=>session.snapshot(),
    setProjection(config){if(session.active)throw Error('기록을 중지한 뒤 카메라 설정을 바꾸세요.');session.projection=config;},
    autoStart(){return safe(start);},autoStop(){if(session.active)stop();}};
  window.haneulmaruCoordinates=api;
  return api;
}
