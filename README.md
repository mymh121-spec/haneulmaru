# 하늘마루 · Haneulmaru

PC에서 합성 3D 지형과 비행체를 렌더링하고 휴대폰으로 WebRTC 영상을 전송하는 데모입니다. 가상환경과 카메라 AR을 전환할 수 있습니다.

**기술 방향: Unity 엔진을 사용하지 않습니다.** Three.js 화면과 순수 JavaScript 시나리오 로직을 분리합니다.

## 가상 데이터 재생 실험실

실제 카메라·센서·탐지·장비 제어와 연결하지 않은 별도 화면입니다. 미리 작성한 가상 도형 3개의 경로를 재생하며, 원점 높이, XYZ/속도, Octree 셀과 경로 부피를 살펴볼 수 있습니다.

```powershell
python experiments/haneulmaru_pro/server.py --local --no-browser
```

브라우저에서 `http://127.0.0.1:8791/replay/`를 엽니다. 기존 스트리밍 서버에서는 `http://127.0.0.1:8792/replay/`로도 열 수 있습니다. 기본 시나리오는 저장소에 포함되어 AWS 계정이나 AI 모델 없이 실행됩니다.

- [화면·데이터 형식·검증 방법](REPLAY.md)
- [AWS API 연결 및 배포 준비](infra/aws/README.md): API Gateway JWT 인증 + Lambda 읽기 전용 시나리오 API. 실제 AWS 배포는 별도입니다.
- 셀 밝기는 **가상 샘플 비율**, 경로 부피는 **작성된 반경**입니다. 확률 추정·미래 예측 결과가 아닙니다.

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

**NUCODE 직접 연동은 아직 미구현입니다.** 기존 프로젝트의 참고 펌웨어·패킷 파서·합성 예제와 연결 지점을 함께 제공합니다. 참고 펌웨어는 현재 보드에 자동 업로드되지 않습니다.

## 렌더링·통신

Three.js/WebGL → canvas.captureStream → WebRTC 비디오 → 휴대폰.
터치·회전·설정은 RTCDataChannel로 전달합니다. Python 서버는 정적 페이지와 SDP/ICE를 처리하며, Cloudflare HTTPS 터널은 페이지/시그널링 전달에 사용합니다. TURN 영상 중계는 없습니다.

폰 카메라는 폰에서만 사용합니다. 카메라 AR은 크로마키 합성으로, 정밀 공간 고정 AR은 아닙니다. 지형과 비행체는 합성 데이터이며 실제 탐지·타격 시스템이 아닙니다.

## 성능 및 한계

- 장면 품질과 영상 전송 화질은 별도 설정입니다. **장면 균형 + 전송 선명 1920**으로 시작하세요.
- 1920·2560픽셀 전송을 PC 두 탭 사이에서 확인했습니다. 실제 휴대폰의 최신 화질 개선 효과는 추가 측정이 필요합니다.
- `Microsoft Basic Render Driver`가 표시되면 RTX 렌더링이 아닙니다.
- 배경/최소화된 탭에서는 FPS가 제한될 수 있습니다.
- AWS 시나리오 API 연결 지점과 SAM 템플릿을 제공합니다. AWS 리소스 배포 및 Bedrock 연결은 하지 않았습니다.

## 포함 구성요소

Git 저장소에서는 Windows x64 cloudflared 2026.10.0을 첫 실행 때 공식 GitHub 배포본에서 다운로드합니다. 제3자 라이선스 및 버전은 `tools/cloudflared`와 `experiments/haneulmaru_pro/vendor`에 있습니다. 프로젝트 전체에 별도의 오픈소스 라이선스를 부여한 것은 아닙니다.

계정·실행 로그·현재 접속 키는 포함하지 않았습니다. `FILES_SHA256.json`은 Git에 저장되는 파일 바이트 기준 체크섬입니다. 변경 파일을 스테이징한 뒤 `python tools/update_checksums.py`로 갱신하고 manifest도 스테이징합니다. Windows 체크아웃의 CRLF 변환 때문에 로컬 파일 바이트와 다를 수 있습니다.
