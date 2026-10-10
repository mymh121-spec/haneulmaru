# 촬영 시작 방향을 북쪽으로 잡는 드론 좌표

`track_yolo_rgb.py`는 탐지·추적 결과에 `system_coordinates`를 추가합니다. 첫 유효한 촬영 자세의 수평 방향이 해당 촬영의 북쪽 0°가 됩니다. 진북이나 GPS 좌표가 아니라 촬영 세션 안의 기준입니다. 새 실행을 시작하면 새 원점과 북쪽 기준이 생깁니다.

좌표축의 순서는 `[동쪽, 북쪽, 위쪽]`, 즉 ENU입니다. 원점은 촬영 시작 카메라 위치 `[0, 0, 0]`이며 현재는 그 자리에 서서 회전한다고 가정합니다. 사용자 이동은 IMU 가속도로 적분하지 않습니다.

## 저장하는 정보

각 드론의 탐지 박스 중심을 이미지 좌표로 읽고, 카메라 모델을 이용해 촬영 방향의 선으로 바꾼 뒤, 촬영 당시 자세로 ENU 좌표계에 회전시킵니다.

| 필드 | 의미 |
|---|---|
| `image_center_px` | 원본 영상의 드론 박스 중심 픽셀 |
| `image_uv` | 원본 영상 크기로 나눈 0–1 화면 좌표 |
| `direction_enu_unit` | 원점에서 드론 쪽으로 향하는 길이 1의 방향 벡터. 단위 없음 |
| `bearing_deg` | 북쪽 0°, 동쪽 90°, 남쪽 180°, 서쪽 270° |
| `elevation_deg` | 수평 0°, 위쪽 양수, 아래쪽 음수 |
| `range_m`, `position_enu_m` | 현재 모두 `null`. 거리와 실제 공간 위치 미정 |
| `session_id` | 이번 촬영의 기준을 구별하는 ID |
| `camera_pose`, `projection` | 사용한 자세 출처와 카메라 모델, 추정 여부 |

길이 1의 방향 벡터를 1m 위치로 취급하지 않습니다. 기준 사진에서 자까지 30cm였다는 정보도 드론 거리로 사용하지 않습니다. 공간 위치는 추후 거리와 이동 위치 정보를 추가할 때 계산합니다.

## 카메라 모델

알려주신 카메라 정보는 화각 약 85°(가로·세로·대각선 기준 미확인), 조리개 f/1.8입니다. 화각 기준을 확인하기 전까지 85°는 방향 계산에 적용하지 않고 기존 임시 수직 화각 55°를 유지합니다. 조리개는 기록용이며 방향 벡터 계산에는 사용하지 않습니다.

`drone_coordinate_config.json`의 기본값은 **수직 화각 55°**입니다. 기존 하늘마루 가상 카메라의 임시 모델이며 **S22의 측정 화각이 아닙니다**. 따라서 현재 방위와 방향 벡터는 임시 추정입니다. 결과의 `projection.source`는 `assumed_vertical_fov`, `calibrated`는 `false`입니다.

추후 실제 촬영 모드의 `fx`, `fy`, `cx`, `cy`, `width`, `height`를 알게 되면 `projection.intrinsics`를 그 값으로 바꿀 수 있습니다. 같은 영상에서 단순 해상도만 바뀌면 내부값을 비례 확대합니다. 화면 크롭, 렌즈 변경, 줌 변경에는 별도 내부값이 필요합니다. 이 버전은 렌즈 왜곡을 교정하지 않습니다. 픽셀 좌표는 화면 CSS 좌표가 아닌 탐지에 사용한 원본 영상 좌표여야 합니다.

수식은 [OpenCV의 카메라 투영 모델](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html)을 따릅니다. AR 화면에 표시하지 않아도 같은 계산을 실행할 수 있습니다.

## 화면 없이 YOLO 좌표 저장

학습을 완료해 `best.pt`가 생긴 GPU PC에서 실행합니다.

```bash
python track_yolo_rgb.py --weights runs_rgb/drone_rgb/weights/best.pt --source 0 --tracker bytetrack --device 0 --jsonl drone_coordinates.jsonl
```

`--show`가 없으므로 창을 열지 않습니다. IMU 또는 외부 자세 입력을 지정하지 않으면 카메라를 고정한 상태라고 가정합니다. 이 경우 카메라를 움직이면 드론 방위 추정이 맞지 않습니다.

NU40와 같이 회전할 때는 기존 NU40 서버가 USB 우선·BLE 연결 유지를 담당하고, YOLO 코드가 그 서버에서 각속도를 읽습니다. 서버는 브라우저 없이 실행할 수 있습니다.

```bash
# 저장소 루트에서 실행
python experiments/haneulmaru_pro/stream_server.py --local --no-browser
```

실행 창에 나온 PC 주소를 연결키(`#...`)까지 그대로 사용합니다. 연결키는 서버를 다시 실행하면 바뀝니다. 아래 `PC주소`는 자리 표시자입니다.

```bash
# photo_color_v1 폴더에서 실행
python track_yolo_rgb.py --weights runs_rgb/drone_rgb/weights/best.pt --source 0 --device 0 --imu-host-url "PC주소" --jsonl drone_coordinates.jsonl
```

독립 `photo_color_v1` 폴더에서는 `--imu-model "방향학습JSON경로"`도 지정합니다. RTSP 등 실시간 영상 주소를 사용할 때는 `--live-source`를 추가합니다. 녹화 영상에 현재 IMU 자세를 붙이지 않습니다.

NU40 좌표 자세는 학습한 오른쪽·왼쪽 축과 위쪽·아래쪽 축에 각속도를 연속 투영해 누적합니다. 방향 표시의 강한 각속도 기준과 화면 감도 설정은 이 계산에 적용하지 않아 느린 회전도 누적합니다. 시작 기울기는 수평으로 가정하고, roll과 사람의 위치 이동은 추정하지 않습니다. 바이어스와 오차가 누적될 수 있는 상대 자세입니다. 센서 단절·200ms 이상의 샘플 간격·보드 재시작 뒤에는 유효한 방향을 내보내지 않으며 새 실행으로 기준을 다시 잡습니다. USB/BLE 전환과 늦게 온 중복 샘플은 북쪽을 바꾸지 않습니다.

## 다른 코드에서 연동

```python
from drone_coordinates import NorthCoordinateSession

coordinates = NorthCoordinateSession({"vertical_fov_deg": 55, "intrinsics": None})
# pose는 heading(시계방향), pitch(위쪽 양수), roll(시계방향)의 도 단위.
# 영상 촬영 시점에 pose를 읽고, 그 프레임을 YOLO에 전달합니다.
pose = {"heading_deg": 37, "pitch_deg": 0, "roll_deg": 0, "valid": True}
tracks = [{"id": 1, "code": "R", "xyxy": [300, 220, 340, 260]}]
data = coordinates.record(tracks, 640, 480, pose)
# 처음 heading 37도가 이번 촬영에서는 북쪽 0도.
```

프로그램이 JSON으로 자세를 공급하려면 `--pose-json pose.json`을 사용합니다. 필드는 `heading_deg`, `pitch_deg`, `roll_deg`, `timestamp_ms`(Unix 밀리초), 선택 `valid`입니다. 임시 파일에 쓴 뒤 교체하는 방식으로 갱신하면 읽는 도중 파일이 끊기지 않습니다. 촬영 시점에서 350ms 이상 떨어진 자세는 무효 처리합니다. 자세는 YOLO 추론 전에 읽어서 추론 시간이 자세에 섞이지 않게 합니다. 카메라 버퍼 지연까지 정밀하게 동기화하는 구현은 후속 단계입니다.

기존 `RGBTracker.update(frame)`의 반환 형식은 그대로입니다. 시스템 좌표를 별도 모듈로 호출할 수 있으므로 다른 코드와 합칠 때 화면·AR 렌더러가 필요하지 않습니다. CLI에서 기존 출력만 필요하면 `--no-system-coordinates`를 사용합니다.

## 확인한 범위

수학과 상태 처리는 GPU·장치 없이 테스트했습니다: 시작 북쪽, 동서남북, 화면 오른쪽/위쪽, 해상도 비례 변경, 거리 미정, 센서 누락, 느린 회전, USB/BLE 중복, 시간 카운터 넘침과 보드 재시작. 학습된 모델이 아직 없어 실제 드론 자동 탐지와 카메라·IMU 동기화 정확도는 확인하지 않았습니다.
