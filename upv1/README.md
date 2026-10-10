# 하늘마루 · Haneulmaru

## upv1 — wan_V1 경량 정리본

최적화본 102개 파일을 이 `upv1/` 폴더에 압축 해제하여 저장했습니다. 저장소를 clone한 뒤 `upv1`을 프로젝트 루트로 사용하세요. 코드 파일은 GitHub에서 바로 열고 수정할 수 있습니다.

실행 코드, YOLO 학습·추적 도구, 좌표 코드, Android 확장 인터페이스를 유지했습니다. 학습용 사진·라벨·미리보기와 디자인 이미지는 실행 폴더에서 분리했으며, 모두 폴더 옆의 `wan_V1_original.zip`에 있습니다. [정리 내역](정리내역.txt)을 참고하세요.

NU40 수신은 `multidevice_app/nu40_transport.py`, 방향 학습 모델은 `multidevice_app/assets/nu40-imu-direction-training.json` 한 곳을 사용합니다. USB 우선·BLE 연결 유지와 학습된 네 방향을 그대로 사용합니다.

학습 PC에서는 원본 ZIP을 `upv1` 폴더 옆에 두고 아래 명령으로 사진·라벨을 복원하세요. 이미 있는 학습 자료는 덮어쓰지 않습니다.

```text
python photo_color_v1/restore_training_data.py
python photo_color_v1/prepare_yolo_rgb.py
```

학습 사진·라벨은 [정리 전 원본 자료](https://github.com/mymh121-spec/haneulmaru/tree/7c81fe5173318f6667d786457434dffdfd3d1b05/photo_color_v1)에 보존되어 있습니다.

## ClikaRT·여러 기기 확장 기반

`multidevice_app/`에 S22 영상 입력, NU40 USB/BLE 입력, 학습된 상하좌우 판정, 인식 엔진, ByteTrack/BoT-SORT 추적, 결과 출력을 분리한 코드가 있습니다. Windows 호스트 실행 코드와 Android 처리 인터페이스를 포함하며 같은 결과 계약을 사용합니다.

**현재는 확장 기반입니다.** ClikaRT SDK·라이선스·학습 모델이 필요하며 실제 ClikaRT 실행과 Android 앱 패키징은 아직 수행하지 않았습니다. S22 실시간 입력에는 별도 카메라 송신기 또는 추후 CameraX 어댑터가 필요합니다. 기존 합성 화면과 별도로 실행합니다.

- [구조·Windows 실행·ClikaRT·Android 연결 안내](multidevice_app/구조와실행.txt)
- [검토 결과와 확인 범위](multidevice_app/검토결과.txt)
- [설정](multidevice_app/config.json)
- [공통 처리 코드](multidevice_app/pipeline.py)
- [Android 연결 계약](multidevice_app/android/HaneulmaruCore.kt)


PC에서 합성 3D 지형과 비행체를 렌더링하고 휴대폰으로 WebRTC 영상을 전송하는 데모입니다. 가상환경과 카메라 AR을 전환할 수 있습니다.

## 빠른 시작

1. 이 저장소를 내려받아 압축을 풀거나 Git으로 clone합니다.
2. Windows x64 환경에서 Python 3.10 이상을 설치합니다.
3. `하늘마루_실행.cmd`를 실행합니다. 처음 실행 시 프로젝트의 `.venv`와 `reportlab`을 설치하고 공식 cloudflared 실행 파일을 내려받아 SHA-256을 검증합니다.
4. PC 렌더러 탭을 화면에 유지합니다.
5. PC와 휴대폰을 같은 Wi-Fi/핫스팟에 연결하고 PC 화면의 QR을 폰에서 엽니다.
6. 폰에서 **PC 연결 시작**을 누릅니다.

서버 종료는 명령창에서 Ctrl+C입니다. 재시작하면 URL/연결 키가 바뀝니다. 기기 간 통신이 차단된 Wi-Fi에서는 연결되지 않을 수 있습니다.

## 팀원용 자료

- [먼저 읽기](00_먼저읽기.txt)
- [NUCODE IMU 연결 합의](01_IMU_연결합의.txt)
- [구조와 검증 범위](02_구조와검증.txt)
- [IMU 참고 파일](IMU_참고)
- [PC 송신·휴대폰 수신 소스](experiments/haneulmaru_pro)

**NU40 DK + NU-IMU-LSM6DS3TR 직접 연동을 지원합니다.** USB 우선·BLE 동시 연결로 학습된 상하좌우 방향에 따라 시점을 회전합니다. [NU40 연결·실행 안내](NU40_연동_사용법.txt)를 참고하세요. Linux에서는 `bash 실행.sh`, Windows에서는 기존 실행 파일을 사용합니다.

## 렌더링·통신

Three.js/WebGL → canvas.captureStream → WebRTC 비디오 → 휴대폰.
터치·회전·설정은 RTCDataChannel로 전달합니다. Python 서버는 정적 페이지와 SDP/ICE를 처리하며, Cloudflare HTTPS 터널은 페이지/시그널링 전달에 사용합니다. TURN 영상 중계는 없습니다.

폰 카메라는 폰에서만 사용합니다. 카메라 AR은 크로마키 합성으로, 정밀 공간 고정 AR은 아닙니다. 지형과 비행체는 합성 데이터이며 실제 탐지·타격 시스템이 아닙니다.

## 성능 및 한계

- 장면 품질과 영상 전송 화질은 별도 설정입니다. **장면 균형 + 전송 선명 1920**으로 시작하세요.
- 1920·2560픽셀 전송을 PC 두 탭 사이에서 확인했습니다. 실제 휴대폰의 최신 화질 개선 효과는 추가 측정이 필요합니다.
- `Microsoft Basic Render Driver`가 표시되면 RTX 렌더링이 아닙니다.
- 배경/최소화된 탭에서는 FPS가 제한될 수 있습니다.
- AWS/Bedrock은 아직 연결되지 않았습니다.

## 포함 구성요소

Git 저장소에서는 Windows x64 cloudflared 2026.10.0을 첫 실행 때 공식 GitHub 배포본에서 다운로드합니다. 제3자 라이선스 및 버전은 `tools/cloudflared`와 `experiments/haneulmaru_pro/vendor`에 있습니다. 프로젝트 전체에 별도의 오픈소스 라이선스를 부여한 것은 아닙니다.

계정·실행 로그·현재 접속 키는 포함하지 않았습니다. `FILES_SHA256.json`은 전달 파일의 체크섬입니다.

## 드론 색상 탐지·영상 추적 코드

`photo_color_v1/`에는 데이터 메타정보와 R/B/G 색상 판별·YOLO 학습·추적 코드가 있습니다. 사진·라벨은 위 복원 명령으로 가져옵니다. **현재는 코드 준비 단계이며, GPU 학습·추론·추적 시험과 하늘마루 화면 연결은 수행하지 않았습니다.**

### 동작 순서

1. **학습 데이터 변환 — `prepare_yolo_rgb.py`**: manifest에서 원본(`none`)을 제외하고, 드론 전체 박스에 `0=R, 1=B, 2=G` 라벨을 붙입니다. 원본별 분할을 유지하며 학습 630장·검증 180장·시험 90장을 만듭니다.
2. **YOLO 학습 — `train_yolo_rgb.py`**: GPU PC에서 YOLO11n을 전이학습합니다. 드론 위치와 색상 클래스를 함께 배우며, 색상 정답을 보존하도록 색조·채도·채널 교환 증강을 끕니다. 학습 후 `best.pt`가 생성됩니다.
3. **영상 탐지·추적 — `track_yolo_rgb.py`**: 새 프레임에서 YOLO가 박스·색상·신뢰도를 예측하고, ByteTrack 또는 BoT-SORT가 프레임 사이의 드론을 연결해 추적 ID를 유지합니다. 추론에는 manifest나 정답 박스를 사용하지 않습니다.
4. **결과 안정화·연동**: ID별 최근 최대 7회 관측에 신뢰도 가중 투표를 적용합니다. 현재 탐지 신뢰도 또는 색상 합의가 부족하면 `UNKNOWN`을 반환합니다. 결과는 ID·색상·박스 좌표이며, 외부 창·MP4·JSONL 또는 Python 함수 반환값으로 사용할 수 있습니다.

별도의 `classify_rgb.py`는 이미 알려진 표식 박스 안의 실제 픽셀을 HSV 기준으로 판별합니다. 이 픽셀 규칙은 YOLO 추적 과정에서 호출하지 않습니다.

- [전체 동작 로직: 흐름도·파일 역할·판별 기준·출력 형식](photo_color_v1/README.md)
- [GPU 학습과 ByteTrack/BoT-SORT 실행 방법](photo_color_v1/YOLO_추적_사용법.txt)
- [별도 픽셀 판별 실행 방법](photo_color_v1/RGB_사용법.txt)

정지사진 데이터로는 탐지 모델을 학습할 수 있지만 추적 ID 유지 성능을 측정할 수 없습니다. 추적 성능은 연속 영상으로 별도 평가해야 합니다.

### 드론 시스템 좌표


촬영을 시작한 카메라 방향을 북쪽 0°로 잡고, 드론 픽셀 위치를 동쪽·북쪽·위쪽(ENU) 방향 벡터로 기록합니다. AR 표식을 그리지 않고도 기록할 수 있습니다. 카메라 AR에서는 수동 위치 지정과 외부 탐지기 입력 API를 제공하고, YOLO CLI에서는 창 없이 JSONL에 저장할 수 있습니다. 기본 화각 55°는 가상 렌더러의 임시 모델이며 실제 카메라 보정값이 아닙니다. 드론 거리와 실제 공간 위치는 미정입니다.

- [시스템 좌표 로직·Python 연동](photo_color_v1/SYSTEM_COORDINATES.md)
- [AR 좌표 기록·브라우저 연동](experiments/haneulmaru_pro/DRONE_COORDINATES.md)

S22 카메라의 사용자 제공 사양은 화각 약 85°(기준 미확인), 조리개 f/1.8입니다. 화각 기준 확인 전까지 임시 수직 화각 55°를 유지합니다.
