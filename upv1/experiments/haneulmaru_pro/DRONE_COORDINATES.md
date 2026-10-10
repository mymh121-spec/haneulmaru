# AR 화면과 독립적인 드론 좌표 기록

카메라 AR 촬영을 시작하면 그 시점의 카메라 수평 방향을 북쪽 0°로 잡습니다. `드론 시스템 좌표` 패널에서 기록 시작·중지와 JSON 저장을 할 수 있습니다. 같은 촬영 중에는 북쪽 기준을 유지하고, 새 기록을 시작하면 기준을 다시 잡습니다. 동쪽·북쪽·위쪽(ENU)의 원점은 시작 카메라 위치이며, 현재는 제자리에서 회전한다고 가정합니다.

`드론 위치 지정`을 누른 뒤 카메라 영상 속 드론을 한 번 누르면 그 픽셀의 방향 좌표를 기록합니다. 화면의 드론에 AR 표식을 그릴 필요는 없습니다. 휴대폰에서 크롭된 카메라 영상의 클릭도 원본 영상 픽셀로 변환합니다. 수동 지정은 각 클릭에 `manual-1` 같은 별도 ID를 주며 자동 추적 ID를 부여하지 않습니다.

YOLO 등 외부 탐지기가 이미 있는 경우 브라우저에서 다음 API를 호출하거나 `haneulmaru:drone-detections` 이벤트를 보내면 같은 좌표 처리를 실행합니다. 현재 저장소에는 학습된 모델이 없으므로 폰 카메라의 자동 탐지를 실행하지는 않습니다.

```javascript
// 이 영상 프레임을 촬영할 때 자세를 읽고, 추론이 끝날 때까지 보존합니다.
const pose = window.haneulmaruCoordinates.capturePose();
const captureTime = Date.now();
// start는 새 촬영을 시작할 때 한 번 호출합니다. 프레임마다 호출하지 않습니다.
window.haneulmaruCoordinates.start(pose);
// 실제 영상의 픽셀 크기와 원본 영상의 탐지 박스 중심을 사용합니다.
// start()가 선택한 자세 입력과 동일한 source의 pose를 보내세요.
window.haneulmaruCoordinates.recordFrame({
  frame: 1, captured_at_ms: captureTime, image_size_px: [1280, 720],
  tracks: [{id: 1, code: 'R', xyxy: [600, 320, 680, 400]}],
  pose // 영상 촬영 시점의 자세를 보존해 비동기 추론 뒤에도 사용합니다.
});
const coordinates = window.haneulmaruCoordinates.latest();
// 같은 함수는 streamBridge.recordDroneDetections(packet)로도 호출 가능합니다.
```

`recordFrame()`은 AR 표식을 표시하지 않고 좌표를 반환합니다. 계산한 프레임은 `haneulmaru:drone-coordinates` 이벤트로 전달되고 `snapshot()`과 `좌표 JSON 저장`에서도 읽을 수 있습니다. 새 탭을 열지 않습니다. 브라우저 메모리에는 최근 2,000프레임을 보관하며, 오래된 프레임 수는 `dropped_frames`에 표시합니다. 전 프레임의 지속 저장이 필요하면 [Python JSONL 경로](../../photo_color_v1/SYSTEM_COORDINATES.md)를 사용하세요.

다른 자세 코드의 값을 직접 넣을 때는 `pose.quaternion`에 Three.js 규약 `[x,y,z,w]`(카메라 앞 방향 `-Z`)을 사용하고 `source`, `timestamp_ms`, `valid`를 함께 제공합니다. `start(pose)`와 각 프레임의 `pose`는 같은 자세 출처여야 합니다.

NU40 모드에서는 좌표 자세를 강한 방향 표시와 별도로 적분합니다. 화면 감도 슬라이더는 좌표의 회전량에 영향을 주지 않습니다. USB 우선·BLE 연결 유지를 그대로 사용하고, PC가 이 자세를 휴대폰에 50ms 간격으로 전달합니다. NU40 자세 입력이 끊기면 `direction_enu_unit`을 `null`로 저장합니다. IMU 시점 초기화 또는 중지/시작으로 센서 자세 기준을 다시 잡고, 좌표 기록도 새로 시작하세요. 자세 입력이 NU40에서 폰 센서로 바뀌면 기록을 새로 시작해야 합니다.

폰 회전 센서가 있으면 해당 자세를 사용합니다. 장치 자세 입력이 없으면 현재 렌더러 시점 또는 고정 카메라를 가정하고 `camera_pose.source`와 `quality`에 표시합니다. 실제 카메라 이동을 측정한 값으로 취급하지 않습니다. NU40 시작 기울기는 수평으로 가정하고 roll을 추정하지 않습니다.

기본 화각 55°는 기존 가상 렌더러의 임시 모델이며 S22 보정값이 아닙니다. 기록을 중지한 상태에서 `setProjection({intrinsics: {fx,fy,cx,cy,width,height}, calibrated: true})`로 알려진 실제 카메라 내부값을 제공할 수 있습니다. 이 버전은 렌즈 왜곡을 교정하지 않습니다. 투영 출처와 `calibrated` 상태는 모든 결과에 기록합니다.

방향은 길이 1의 단위 없는 ENU 벡터와 방위·고도각입니다. 드론 거리와 실제 3차원 위치는 각각 `range_m: null`, `position_enu_m: null`로 둡니다. 자 사진의 30cm 촬영 거리는 드론 거리에 적용하지 않습니다. 북쪽은 실제 나침반 북쪽이나 GPS 기준이 아니라 처음 촬영 방향에 붙인 이름입니다.

좌표 모듈의 카메라 규약은 [Three.js Camera](https://threejs.org/docs/#api/en/cameras/Camera), 투영은 [OpenCV 카메라 모델](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html)을 기준으로 합니다.

## S22 카메라 사양 추가 정보

알려주신 카메라 정보는 화각 약 85°(가로·세로·대각선 기준 미확인), 조리개 f/1.8입니다. 화각 기준을 확인하기 전까지 85°는 방향 계산에 적용하지 않고 기존 임시 수직 화각 55°를 유지합니다. 조리개는 기록용이며 방향 벡터 계산에는 사용하지 않습니다.
