// No rendering dependency: camera quaternions use Three.js [x,y,z,w], -Z forward.
const finite = value => typeof value === 'number' && Number.isFinite(value);
const unit = v => {const n=Math.hypot(...v);if(!n||!v.every(finite))throw Error('유효한 방향 벡터가 필요합니다.');return v.map(x=>x/n);};
const degrees = r => r*180/Math.PI;
const bearing = (east,north) => {const a=(degrees(Math.atan2(east,north))+360)%360;return a>=360-1e-9?0:a;};
function quaternion(value) {
  if(!Array.isArray(value)||value.length!==4||!value.every(finite))throw Error('촬영 시점의 카메라 자세가 필요합니다.');
  return unit(value);
}
function rotate(v,q) {
  const [x,y,z,w]=q,[a,b,c]=v;
  const tx=2*(y*c-z*b),ty=2*(z*a-x*c),tz=2*(x*b-y*a);
  return [a+w*tx+y*tz-z*ty,b+w*ty+z*tx-x*tz,c+w*tz+x*ty-y*tx];
}
export function projectionParameters(config,width,height) {
  if(!Number.isInteger(width)||!Number.isInteger(height)||Math.min(width,height)<=0)throw Error('영상의 실제 픽셀 크기가 필요합니다.');
  if(config.intrinsics) {
    const k=config.intrinsics;
    if(!['fx','fy','cx','cy','width','height'].every(key=>finite(k[key]))||Math.min(k.fx,k.fy,k.width,k.height)<=0)throw Error('유효한 카메라 내부값이 필요합니다.');
    return {fx:k.fx*width/k.width,fy:k.fy*height/k.height,cx:k.cx*width/k.width,cy:k.cy*height/k.height,source:'supplied_intrinsics',calibrated:config.calibrated===true};
  }
  const fov=config.vertical_fov_deg;
  if(!finite(fov)||fov<=0||fov>=170)throw Error('카메라 화각 또는 내부값이 필요합니다.');
  const focal=height/(2*Math.tan(fov*Math.PI/360));
  return {fx:focal,fy:focal,cx:width/2,cy:height/2,source:'assumed_vertical_fov',vertical_fov_deg:fov,calibrated:false};
}

export class NorthCoordinateSession {
  constructor(projection={vertical_fov_deg:55,intrinsics:null}) {this.projection=projection;this.active=false;this.frames=[];}
  start(pose) {
    if(pose.valid===false)throw Error('카메라 자세 입력을 기다린 뒤 좌표 기록을 시작하세요.');
    const q=quaternion(pose.quaternion),forward=rotate([0,0,-1],q);
    if(Math.hypot(forward[0],forward[2])<1e-6)throw Error('북쪽 기준을 잡을 때 카메라를 수평 방향으로 향하게 해주세요.');
    this.initialHeading=Math.atan2(forward[0],-forward[2]);
    this.poseSource=pose.source||'external_camera_pose';this.sessionId=crypto.randomUUID();
    this.startedAt=Date.now();this.active=true;this.frames=[];this.droppedFrames=0;this.latest=null;
    return this.sessionId;
  }
  stop() {this.active=false;}
  record(packet,pose) {
    if(!this.active)throw Error('좌표 기록을 먼저 시작하세요.');
    if((pose.source||'external_camera_pose')!==this.poseSource)throw Error('자세 입력이 바뀌었습니다. 좌표 기록을 다시 시작하세요.');
    const [width,height]=packet.image_size_px||[],projection=projectionParameters(this.projection,width,height);
    const q=quaternion(pose.quaternion);
    const captured=packet.captured_at_ms??Date.now();
    if(!finite(captured))throw Error('촬영 시간이 올바르지 않습니다.');
    const timestamp=pose.timestamp_ms;
    const valid=pose.valid!==false&&(!finite(timestamp)||Math.abs(captured-timestamp)<=350);
    const enu = cameraVector => {
      const [x,y,z]=rotate(cameraVector,q),e=x,n=-z;
      const c=Math.cos(this.initialHeading),s=Math.sin(this.initialHeading);
      return unit([e*c-n*s,e*s+n*c,y]);
    };
    if(!Array.isArray(packet.tracks))throw Error('드론 탐지 목록이 필요합니다.');
    const observations=packet.tracks.map(track=>{
      const b=track.xyxy;
      if(!Array.isArray(b)||b.length!==4||!b.every(finite)||!(0<=b[0]&&b[0]<=b[2]&&b[2]<=width&&0<=b[1]&&b[1]<=b[3]&&b[3]<=height))throw Error('드론 박스가 영상 범위를 벗어났습니다.');
      const u=(b[0]+b[2])/2,v=(b[1]+b[3])/2;
      const ray=valid?enu([(u-projection.cx)/projection.fx,(projection.cy-v)/projection.fy,-1]):null;
      const horizontal=ray?Math.hypot(ray[0],ray[1]):0;
      return {id:track.id??null,code:track.code||'UNKNOWN',image_center_px:[u,v],image_uv:[u/width,v/height],
        ray_origin_enu_m:[0,0,0],direction_enu_unit:ray,
        bearing_deg:ray&&horizontal>1e-9?bearing(ray[0],ray[1]):null,
        elevation_deg:ray?degrees(Math.atan2(ray[2],horizontal)):null,
        range_m:null,position_enu_m:null,status:valid?'direction_estimate':'pose_unavailable'};
    });
    const data={schema_version:1,session_id:this.sessionId,frame:packet.frame??null,captured_at_ms:captured,
      reference:{axes:['east','north','up'],north:'initial_camera_heading_assumed',origin_enu_m:[0,0,0],origin_mode:'stationary_camera_assumed',initial_heading_deg:degrees(this.initialHeading)},
      camera_pose:{quaternion_three_xyzw:q,source:this.poseSource,valid,quality:pose.quality||'relative_camera_pose_estimate'},
      image_size_px:[width,height],projection,observations};
    this.latest=structuredClone(data);this.frames.push(this.latest);
    if(this.frames.length>2000){this.frames.shift();this.droppedFrames++;}
    return data;
  }
  snapshot() {return structuredClone({schema_version:1,session_id:this.sessionId??null,started_at_ms:this.startedAt??null,active:this.active,dropped_frames:this.droppedFrames??0,frames:this.frames});}
}

// Coordinate pose is integrated independently of gesture strength / UI gain.
export class GyroPose {
  constructor(model) {
    if(model?.format!=='nu40-imu-direction-model'||model.units!=='degrees_per_second')throw Error('NU40 각속도 학습 모델이 필요합니다.');
    const v=Object.fromEntries(['up','down','left','right'].map(k=>{const a=model.directions?.[k];if(!Array.isArray(a)||a.length!==3)throw Error('3축 학습 벡터가 필요합니다.');return [k,unit(a)];}));
    this.horizontal=unit(v.right.map((x,i)=>x-v.left[i]));this.vertical=unit(v.up.map((x,i)=>x-v.down[i]));
    this.coupling=this.dot(this.horizontal,this.vertical);
    if(1-this.coupling**2<.1)throw Error('가로/세로 회전축을 구분할 수 없습니다.');
    this.reset();
  }
  dot(a,b){return a.reduce((sum,x,i)=>sum+x*b[i],0);}
  reset(){this.heading=0;this.pitch=0;this.previous=null;this.session=null;this.source=null;this.valid=false;this.uncertain=false;}
  consume(state) {
    const p=state?.sample;
    if(!p||!finite(state.age_ms)||state.age_ms<0||state.age_ms>350||!['USB','BLE'].includes(state.source)){
      if(this.previous!==null)this.uncertain=true;this.previous=null;this.valid=false;return this.snapshot();
    }
    const v=[p.gx,p.gy,p.gz],stamp=p.t_ms;
    if(!Number.isInteger(stamp)||stamp<0||stamp>0xffffffff||!v.every(finite)){this.valid=false;this.uncertain=true;return this.snapshot();}
    if(this.session!==null&&state.session!==this.session){this.uncertain=true;this.previous=null;}
    this.session=state.session;
    const dt=this.previous!==null?(stamp-this.previous+0x100000000)%0x100000000:0;
    if(dt>0x80000000){if(0x100000000-dt>200||state.source===this.source){this.previous=stamp;this.uncertain=true;this.valid=false;}return this.snapshot();}
    this.previous=stamp;this.source=state.source;
    if(dt>200)this.uncertain=true;
    else if(dt){
      const h=this.dot(v,this.horizontal),u=this.dot(v,this.vertical),denominator=1-this.coupling**2;
      this.heading=(this.heading+(h-this.coupling*u)/denominator*dt/1000+360)%360;
      this.pitch=Math.max(-89.9,Math.min(89.9,this.pitch+(u-this.coupling*h)/denominator*dt/1000));
    }
    this.valid=!this.uncertain;return this.snapshot();
  }
  snapshot(){
    const h=this.heading*Math.PI/360,p=this.pitch*Math.PI/360;
    return {quaternion:[Math.cos(h)*Math.sin(p),-Math.sin(h)*Math.cos(p),Math.sin(h)*Math.sin(p),Math.cos(h)*Math.cos(p)],
      valid:this.valid,source:'NU40_trained_axis_gyro_integration',timestamp_ms:Date.now(),
      quality:this.valid?'relative_unleveled_gyro_estimate':'imu_gap_restart_required'};
  }
}
