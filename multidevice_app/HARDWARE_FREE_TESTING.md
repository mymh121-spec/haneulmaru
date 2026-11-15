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

## 작업자 제한 시간과 복구

`config.json`의 `engine`에 `startup_timeout_s`(기본 30초), `request_timeout_s`(기본 5초)를
추가할 수 있습니다. 추적기는 기본 5초이며 Python에서 `RustTracking(request_timeout_s=...)`로 조정합니다.
제한 시간은 전송과 응답, 종료 정리를 포함하는 예산입니다. 최대 0.5초(짧은 예산은 25%)를
종료 정리에 예약합니다. 오류에는 모델 시작/프레임 추론/추적 단계와 send/response 원인이 표시됩니다.
OS 종료가 기한 내 끝나지 않으면 별도의 cleanup timeout 오류를 반환합니다.

시간 초과된 추론 작업자는 종료되므로 새 `RustEngine`을 만든 후 사용해야 합니다.
`Pipeline`은 마지막 성공 프레임을 유지하여 호출자가 같은 프레임을 재시도할 수 있게 합니다.
추적기에 관측을 넘긴 뒤 실패하면 다음 호출에서 추적기를 초기화하고 새 `session_id`를 발급합니다.
따라서 ID는 반드시 `(session_id, id)`로 구별해야 합니다. CLI는 오류를 알리고 종료하며 자동 재시도하지 않습니다.

## 데이터 가져오기 정책

YOLO 박스는 경계를 벗어나면 거부합니다. COCO는 기존 정책대로 일부만 벗어난 박스를 자르고
완전히 밖에 있는 박스는 거부합니다. COCO 주석의 너비/높이는 실제 이미지와 일치해야 합니다.
SHA-256이 같은 파일은 분할 안팎 모두 거부합니다. 재압축이나 변형된 유사 이미지 판별은 포함하지 않습니다.

정상 가져오기 결과에는 `validation_report.json`을 생성합니다. 분할별 이미지/박스 수,
원본 경로, SHA-256, 실제 크기, COCO에서 잘린 박스 수를 기록합니다.
실패하면 오류에 원인을 표시하고 최종 데이터셋 폴더를 만들지 않습니다.

합성 테스트는 소프트웨어 동작을 검증합니다. 실제 영상의 인식률이나 S22/NU40 연결 성능을
측정한 결과는 아닙니다.
