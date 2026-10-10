import {streamBridge as bridge} from './app.js';
import {DirectionInput, labels} from './imu-direction.js';
import {GyroPose} from './drone-coordinates.js';

const panel=document.createElement('section');
panel.className='imu-panel';
panel.innerHTML='<b>NUCODE · IMU</b><p id="imuStatus" role="status">연결 확인 중</p><p id="imuDirection" class="imu-direction">대기</p><div class="modes"><button id="imuToggle" disabled>연결 준비 중</button><button id="imuCenter">시점 초기화</button></div><label>회전 감도 <output id="imuGainText">1.0×</output><input id="imuGain" type="range" min="0.2" max="2" step="0.1" value="1"></label><p class="muted">USB 우선 · BLE 연결 유지<br>학습한 상하좌우 움직임으로 시점을 회전합니다.</p>';
document.getElementById('panel').prepend(panel);
const $=id=>document.getElementById(id);
const token=location.hash.slice(1)||sessionStorage.getItem('pro-stream-host')||'';
let classifier, coordinatePose, enabled=false, lastDirectionAt=0, stopped=false;
async function api(path) {
  const response=await fetch('/stream/'+path,{headers:{Authorization:'Bearer '+token},cache:'no-store',signal:AbortSignal.timeout(2000)});
  if(!response.ok) throw Error(response.status===401?'연결 주소가 만료되었습니다. 실행 창의 PC 주소로 다시 여세요.':'IMU 연결 응답 오류 ('+response.status+')');
  return response.json();
}
function setEnabled(value) {
  enabled=value; classifier?.reset(); coordinatePose?.reset(); bridge.setImuEnabled(value);
  $('imuToggle').textContent=value?'IMU 중지':'IMU 시작';
  $('imuDirection').textContent=value?'대기':'중지';
}
$('imuToggle').onclick=()=>setEnabled(!enabled);
$('imuCenter').onclick=()=>{bridge.resetView();classifier?.reset();coordinatePose?.reset();$('imuDirection').textContent=enabled?'대기':'중지';};
$('imuGain').oninput=()=>$('imuGainText').textContent=Number($('imuGain').value).toFixed(1)+'×';
async function poll() {
  if(stopped)return;
  try {
    const state=await api('imu');
    const usb=state.sources.USB,ble=state.sources.BLE;
    $('imuStatus').textContent=(state.source?state.source+' 수신 중':'IMU 데이터 대기')+' · BLE '+(ble.fresh?'수신 유지':ble.connected?'연결됨':'재연결 대기');
    $('imuStatus').title='USB: '+usb.message+' / BLE: '+ble.message;
    if(!classifier) {
      const model=await api('imu-model');
      classifier=new DirectionInput(model);
      coordinatePose=new GyroPose(model);
      $('imuToggle').disabled=false;
      setEnabled(true);
    }
    bridge.setCoordinateImuPose(coordinatePose.consume(state));
    const motion=classifier.consume(state,Number($('imuGain').value));
    if(enabled&&motion&&!document.hidden) {
      bridge.imuRotate(motion.yaw,motion.pitch);
      $('imuDirection').textContent=labels[motion.key];lastDirectionAt=performance.now();
    } else if(enabled&&(!state.sample||performance.now()-lastDirectionAt>180)) $('imuDirection').textContent='대기';
  } catch(error) {
    if(coordinatePose)bridge.setCoordinateImuPose(coordinatePose.consume({}));
    classifier?.reset();$('imuStatus').textContent=error.message;$('imuDirection').textContent=enabled?'대기':'중지';
  } finally {if(!stopped)setTimeout(poll,40);}
}
document.addEventListener('visibilitychange',()=>classifier?.reset());
addEventListener('beforeunload',()=>{stopped=true;});
poll();
