# 하늘마루 · Haneulmaru

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


## 정지사진 색상 표식 R / B / G

[photo_color_v1](photo_color_v1/)에 사진 1,200장과 라벨, 별도 색상 판별 코드가 있습니다.
원본(none)을 제외한 표식 사진 900장을 픽셀 색상으로 R/B/G로 구분합니다.
표식 위치는 manifest 좌표를 사용하며, 자동 객체 탐지나 학습 모델은 아닙니다.
[실행 방법](photo_color_v1/RGB_사용법.txt)을 참고하세요. 이번 추가에서는 테스트를 실행하지 않았습니다.


### YOLO 드론 색상 탐지와 영상 추적

[YOLO 학습·추적 사용법](photo_color_v1/YOLO_추적_사용법.txt): YOLO11로 R/B/G 드론 탐지를 학습하고 ByteTrack 또는 BoT-SORT로 추적합니다. GPU PC 실행용 코드이며 현재 학습된 모델은 포함하지 않습니다.
