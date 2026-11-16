# 하드웨어 없이 검증하기

카메라, S22, NU40, GPU 없이 Python과 CPU Rust 작업자로 실행합니다.
저장소 루트에서 아래 명령을 사용합니다.

## 설치와 전체 검사

```sh
python -m pip install -r multidevice_app/requirements.txt onnxruntime==1.23.2
cargo test --manifest-path multidevice_app/rust/Cargo.toml --locked
cargo build --manifest-path multidevice_app/rust/Cargo.toml --release --locked
python -m unittest discover -s multidevice_app -t . -p "test_*.py" -v
```

Linux 또는 시스템 ONNX Runtime이 없는 Windows에서는 `HANEULMARU_ORT_LIB`에
설치한 `onnxruntime/capi/libonnxruntime.so.*` 또는 `onnxruntime/capi/onnxruntime.dll`의
절대 경로를 지정합니다. Windows는 기존 `multidevice_app/bin/haneulmaru.exe`를 우선 사용합니다.
소스 빌드를 검증하려면 빌드한 `rust/target/release/haneulmaru.exe`로 해당 파일을 교체합니다.
CI는 Windows와 Linux에서 소스를 빌드한 뒤 테스트합니다.
로컬 LLM은 별도 `local-llm` 작업에서 공식 mistralrs SDK를 빌드하고, Rust에서 만든 작은 미학습 GGUF로 실제 토큰 생성까지 검사합니다. 외부 가중치는 다운로드하지 않습니다. [Rust LLM 실행과 검증 경계](local_llm/README.md)
기본 Rust 탐지+추적 통합 경로와 오프라인 Rust 평가도 이 빌드로 검사합니다.
저장소의 Windows 실행 파일도 새 IMU 프로토콜과 strict 추적 정책을 지원하는 빌드로 갱신했습니다.

ONNX Runtime 없이도 변경된 오류 처리와 데이터 검사는 실행할 수 있습니다.

```sh
python -m unittest multidevice_app.test_worker multidevice_app.test_pipeline multidevice_app.test_dataset_validation multidevice_app.test_remote_datasets -v
```

## 검사 내용

- 실제 Python 자식 프로세스를 이용해 시작 응답 없음, 프레임 미수신, 수신 후 응답 없음,
  잘못된 JSON/추적 ID, EOF를 재현합니다. 종료된 프로세스와 파이프 정리도 확인합니다.
- 탐지 실패 후 같은 프레임 재시도, 성공 프레임 중복 거부, 추적 응답 유실 후 새 세션 발급을 검사합니다.
- 이름을 바꾼 동일 이미지, 영상 밖 YOLO 박스, COCO 이미지 크기 불일치, 손상된 이미지를 거부합니다.
  기존 출력 보호와 실패 시 임시 출력 정리도 검사합니다.
- 실제 Rust 추론기에 합성 ONNX를 넣어 색상, 박스, 교차와 가림 중 ID 유지를 검사합니다.
- 동봉 AVI를 OpenCV로 읽어 48프레임의 177건을 `demo/expected-tracks.jsonl`의
  프레임 번호, 시간, 클래스, 박스, ID와 비교합니다. OpenCV가 없으면 이 검사만 건너뜁니다.
- 모델 해시/클래스 순서 변경을 작업자 시작 전에 거부하고, 표시 별칭을 바꿔도 위 177건이 유지되는지 검사합니다.
- Rust 추적기의 strict/allow 정책에서 클래스 교체, 낮은 신뢰도, UNKNOWN, 색상 깜빡임을 검사합니다.
- 동기화 응답과 IMU 샘플을 같은 쓰기에 묶어 보내 첫 샘플이 버퍼 전환에서 사라지지 않는지 검사합니다.
- 통합 Rust 파이프라인의 48프레임 결과와 세션/복구를 기존 경로에 대조하고, Rust 평가를 보존한 Python 기준 구현과 대조합니다.
- 실제 60fps FFV1 파일 600프레임에서 프레임 순서/시각, 2,385관측, IMU 만료/복구를 검사합니다.
  격리 입력의 바이너리 부분 전송, 중간 EOF/정지, 크기 제한과 720p에서 회색 여백 오탐이 없는지도 검사합니다.
- 로컬 UDP로 실제 Rust IMU 작업자에 데이터를 보내 Python 읽기를 600ms 지연시킵니다.
  오래된 값이 되살아나지 않는지와 다음 새 패킷의 복구를 검사합니다. 루프백 UDP 허용이 필요합니다.

## 모델 배포 기록과 표시 이름

학습 시 클래스 ID 순서를 JSON 배열로 보관합니다. 예: `training-classes.json`의
`["crate", "cart"]`. 학습/내보내기에 실제 사용한 클래스 목록을 사용해야 합니다.
모델이 준비된 뒤 기존 프로필에서 새 배포 프로필을 생성합니다.

```sh
python -m multidevice_app.model_profile --profile profile.json --training-classes training-classes.json --output deployed.json
python -m multidevice_app.run --profile deployed.json --source recording.avi --recording --no-imu
```

새 프로필의 `deployment`에는 `model_sha256`와 `classes`를 저장합니다.
실행할 때 실제 가중치 해시와 클래스 이름/순서를 대조하므로 `--model`로 다른 파일을 지정해도
해시가 다르면 시작하지 않습니다. 기존 출력 파일은 덮어쓰지 않습니다.
이 기록은 실수로 모델/프로필을 섞는 것을 검출하며, 모델의 인식률이나 기록의 출처를 인증하지 않습니다.

화면의 이름만 바꿀 때는 프로필에 `"display_names": {"crate": "상자", "cart": "카트"}`를
추가합니다. `classes`는 학습 클래스 순서 그대로 유지합니다. JSONL의 `code`, `raw_code`, ID는
유지되고 `display_name`이 별도로 제공됩니다. 낮은 신뢰도의 UNKNOWN은 별칭으로 숨기지 않습니다.
기록 없는 기존 프로필은 계속 지원하며 `engine.model_verification.status=unverified`와
CLI 안내를 제공합니다. 대조에 성공하면 `verified`와 모델 해시를 기록합니다.

## 클래스 간 ID 연결 정책

프로필 또는 `config.json`의 `engine`에 `"class_change_policy": "strict"`를 지정하면
서로 다른 클래스의 관측을 같은 추적 ID에 연결하지 않습니다. UNKNOWN도 별도로 취급합니다.
현재 strict는 Rust 추적기에서 지원합니다. ByteTrack/BoT-SORT와 함께 지정하면 시작 시 오류를 표시합니다.
`tracking=none`은 ID를 생성하지 않습니다.

기본 `allow`는 기존 동작입니다. 한 드론의 R/B/G 색상 관측이 순간적으로 바뀌어도 ID를 유지합니다.
표시 별칭은 두 정책 모두에서 연결 판단에 사용하지 않습니다.

## IMU 수신 시간과 지연

Python과 Rust는 시작 시 요청/응답으로 시간 기준을 설정한 뒤, Rust의 UDP 수신 경과 시간을 전달합니다.
Python은 요청 전 고해상도 `perf_counter` 시각을 기준으로 샘플 나이를 계산합니다. 동기화 전달 시간만큼 보수적으로
더 오래된 값으로 판단하며, 프로세스 시작 지연과 파이프 읽기 시각을 새 수신 시각으로 사용하지 않습니다.
수신 후 350ms를 넘긴 값은 만료됩니다. 기기 자체의 측정 시각과 카메라 노출 시각 동기화는 별도 과제입니다.

IMU 프로토콜 2가 없는 구버전 Rust 작업자는 재빌드 안내와 함께 거부합니다.
`WifiImu(startup_timeout_s=5)`로 시작 응답과 시간 기준 동기화 각각의 제한 시간을 조정할 수 있습니다.
응답이 없거나 프로토콜이 맞지 않으면 작업자를 종료합니다.

## 작업자 제한 시간과 복구

`config.json`의 `engine`에 `startup_timeout_s`(기본 30초), `request_timeout_s`(기본 5초)를
추가할 수 있습니다. 추적기는 기본 5초이며 Python에서 `RustTracking(request_timeout_s=...)`로 조정합니다.
제한 시간은 전송과 응답, 종료 정리를 포함하는 예산입니다. 최대 0.5초(짧은 예산은 25%)를
종료 정리에 예약합니다. 오류에는 모델 시작/프레임 추론/추적 단계와 send/response 원인이 표시됩니다.
OS 종료가 기한 내 끝나지 않으면 별도의 cleanup timeout 오류를 반환합니다.

시간 초과된 추론 작업자는 종료되므로 새 `RustEngine`을 만든 후 사용해야 합니다.
`Pipeline`은 마지막 성공 프레임을 유지하여 호출자가 같은 프레임을 재시도할 수 있게 합니다.
추적기에 관측을 넘긴 뒤 실패하면 다음 호출에서 추적기를 초기화하고 새 `session_id`를 발급합니다.
따라서 ID는 반드시 `(session_id, id)`로 구별해야 합니다. 기본 CLI는 오류를 알리고 종료합니다.
`--max-restarts N`을 지정하면 입력/작업자에 공통 예산을 적용해 최대 N회 복구하고 새 추적 세션을 발급합니다.
라이브 입력은 디코더를 격리해 열기/읽기에도 `--io-timeout`을 적용합니다.
녹화 파일 EOF는 종료이며 처음부터 자동 복구하지 않습니다. 자세한 사용법은 [오프라인 작업 안내](OFFLINE_WORKBENCH.md)를 참고하세요.

## 데이터 가져오기 정책

YOLO 박스는 경계를 벗어나면 거부합니다. COCO는 기존 정책대로 일부만 벗어난 박스를 자르고
완전히 밖에 있는 박스는 거부합니다. COCO 주석의 너비/높이는 실제 이미지와 일치해야 합니다.
SHA-256이 같은 파일은 분할 안팎 모두 거부합니다. 재압축이나 변형된 유사 이미지 판별은 포함하지 않습니다.

정상 가져오기 결과에는 `validation_report.json`을 생성합니다. 분할별 이미지/박스 수,
원본 경로, SHA-256, 실제 크기, COCO에서 잘린 박스 수를 기록합니다.
실패하면 오류에 원인을 표시하고 최종 데이터셋 폴더를 만들지 않습니다.

합성 테스트는 소프트웨어 동작을 검증합니다. 실제 영상의 인식률이나 S22/NU40 연결 성능을
측정한 결과는 아닙니다.
