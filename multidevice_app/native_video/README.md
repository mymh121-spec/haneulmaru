# Rust 영상 입력과 실행 관리

`haneulmaru-video`는 Python 프로세스 없이 영상 입력부터 검출, 추적, JSONL 출력까지 실행하는 headless CLI다. 기존 `run.py` 실행 경로도 유지한다. OpenCV는 C++ 라이브러리이며 Rust 바인딩으로 호출한다. 코덱과 ONNX Runtime까지 순수 Rust로 다시 구현한 것은 아니다.

## 이번에 옮긴 범위

- 파일, 카메라 번호, URL 입력과 0/90/180/270도 회전: 별도 Rust 디코더 프로세스.
- 프레임 번호와 영상 시간 관리: PTS 우선, 잘못되거나 정지한 PTS는 FPS, FPS도 없으면 디코딩 경과 시간으로 보완.
- 기존 Rust ONNX 검출, 추적 작업자의 시작, 요청, 종료와 응답 검증. 전달은 JSON 헤더와 BGR 바이트이며 base64를 사용하지 않는다.
- 입력과 추론의 시작, 쓰기, 읽기 제한 시간. 중단 시 자식 프로세스 종료와 회수. 한 번에 한 프레임만 처리하므로 무제한 대기열이 없다.
- 라이브 입력 재연결과 추론 작업자 재시작에 공통 `--max-restarts` 예산 적용. 지수 백오프와 새 추적 세션. 녹화 입력 오류는 중복 재생하지 않고 실패한다.
- 구간 재생, 반복, 최대 프레임 수, `--max-fps`, JSONL 출력, Ctrl+C 종료.
- 기존 Rust UDP IMU 수신과 분류. 동기화된 수신기 경과 시간 기준 350ms 만료를 적용한다. 네트워크 도착 시각 기반 근사 정렬이며 촬영 시각 동기화를 보장하지 않는다.
- 기존 모델 프로파일, 클래스 이름, 별칭, 클래스 변경 정책, 배포 해시와 클래스 순서 검증.

미리보기, 좌표 변환, 녹화 IMU 로그 재생, 검출 캐시, 평가 UI, 학습/데이터 도구는 기존 Python 경로를 사용한다. Rust CLI에 없는 옵션은 오류로 처리한다. 녹화 영상에는 동기화된 IMU가 없는 것으로 명시한다. 이 단계에서 기존 `.cmd` 미리보기 실행기를 교체하지 않았다.

## 준비물과 빌드

저장소 루트에서 실행한다. Rust/Cargo와 네이티브 C++ 도구가 필요하다. Windows는 MSVC와 Windows SDK, LLVM의 `clang.exe`, OpenCV SDK, libclang이 필요하다. Linux는 C++ 도구, OpenCV 개발 패키지, Clang/libclang이 필요하다. Rust 패키지는 `opencv = 0.102.0`에 고정하고 `Cargo.lock`을 커밋한다. 외부 프로젝트를 포크하지 않는다.

Windows PowerShell:

```powershell
# 먼저 Visual Studio C++ Build Tools, Rust, LLVM을 설치한다.
# -Install은 OpenCV 4.11.0 공식 SDK를 해시 검증 후 내려받고,
# 설치된 LLVM의 clang.exe와 libclang.dll을 함께 사용한다.
# LLVM 18 부분 설치본에 DLL이 없을 때만 Python/pip로 libclang 18.1.1을 보완한다.
# clang.exe 자체는 LLVM 설치본 또는 PATH에 있어야 한다.
& ./multidevice_app/native_video/windows-env.ps1 -Install
cargo build --release --locked --manifest-path multidevice_app/rust/Cargo.toml
cargo build --release --locked --manifest-path multidevice_app/native_video/Cargo.toml
```

이미 SDK를 준비했다면 새 터미널마다 `windows-env.ps1`을 `-Install` 없이 실행한다. Clang 실행 파일과 libclang의 주 버전을 맞춰야 한다. Windows CI의 LLVM 20과 로컬 LLVM 18처럼 설치 환경이 달라도 해당 LLVM의 라이브러리를 우선 선택한다. 스크립트 대신 `OPENCV_INCLUDE_PATHS`, `OPENCV_LINK_PATHS`, `OPENCV_LINK_LIBS`, `LIBCLANG_PATH`와 `PATH`를 직접 지정해도 된다. 빌드용 `.native-deps`와 `target`은 Git에 올리지 않는다.

Ubuntu:

```bash
sudo apt-get install build-essential pkg-config libopencv-dev libclang-dev clang
cargo build --release --locked --manifest-path multidevice_app/rust/Cargo.toml
cargo build --release --locked --manifest-path multidevice_app/native_video/Cargo.toml
```

실행에는 두 Rust 실행 파일, OpenCV 런타임과 동영상 코덱 플러그인, ONNX Runtime 공유 라이브러리, 로컬 ONNX 모델, 설정 파일이 필요하다. Python, Clang, Cargo는 빌드가 끝난 실행 환경에는 필요하지 않다. Windows에서는 OpenCV SDK의 `x64/vc16/bin`을 PATH에 넣고, `opencv_videoio_ffmpeg4110_64.dll`도 함께 유지한다. DLL을 공식 SDK에서 가져오며 재배포 시 해당 라이선스를 함께 확인한다.

## 실행

Windows 예시. `HANEULMARU_ORT_LIB`에는 ONNX Runtime CPU DLL의 절대 경로를 지정한다. 개발 환경에서는 기존 Python 패키지의 DLL을 직접 사용할 수도 있으며 Python 실행 프로세스가 생기지는 않는다.

```powershell
& ./multidevice_app/native_video/windows-env.ps1
$env:HANEULMARU_ORT_LIB = (Resolve-Path '.venv/Lib/site-packages/onnxruntime/capi/onnxruntime.dll').Path
./multidevice_app/native_video/target/release/haneulmaru-video.exe `
  --worker multidevice_app/rust/target/release/haneulmaru.exe `
  --config multidevice_app/demo/config.json `
  --source multidevice_app/demo/multi-tracking.avi `
  --max-fps 60 --jsonl native-result.jsonl
```

Linux에서는 `.exe` 없는 실행 파일과 `libonnxruntime.so`를 지정한다. 기본 작업자는 Windows `multidevice_app/bin/haneulmaru.exe`, Linux `multidevice_app/rust/target/release/haneulmaru`다. `--worker`로 새 빌드 경로를 명시할 수 있다.

`--profile multidevice_app/demo/profile.json`으로 일반 클래스 프로파일을 사용한다. 실제 카메라는 기본 설정과 `--source 0`, URL은 `--source URL`을 쓴다. IMU를 사용하지 않으면 `--no-imu`, 라이브 URL을 녹화처럼 처리하려면 `--recording`을 지정한다. 설정의 `camera.live`와 기존 파일 존재 여부도 입력 모드를 결정한다.

`--start-ms 100 --end-ms 1000 --repeat 2`는 시작 포함, 끝 제외 구간을 두 번 재생한다. 반복마다 프레임 번호는 원래 영상 번호로 돌아가고 `session_id`가 바뀐다. `--max-frames 600`은 모든 반복을 합친 출력 개수다. 기존 JSONL 파일은 덮어쓰지 않는다. `--flush-every` 기본값은 1이다.

`--io-timeout`은 디코더 시작/프레임 요청 제한 시간이며 기본 5초다. 추론 시작과 요청 제한은 `engine.startup_timeout_s`(30초), `engine.request_timeout_s`(5초)다. `--max-restarts` 기본값 0, 최대 20이며 재연결과 추론 재시작이 예산을 공유한다. Ctrl+C는 대기, 백오프를 취소한다.

`--max-fps 60`은 최대 처리 시작 빈도다. 각 프레임의 실제 시작 시각을 기준으로 다음 시작까지 최소 16.67ms를 기다린다. 프레임을 버리거나 밀린 프레임을 한꺼번에 실행하지 않는다. OS 대기 오차와 처리 지연 때문에 실제 출력이 정확히 60fps로 고정되지는 않는다. 기본값 0은 제한 없음이다.

## 하드웨어 없는 검증

Windows의 SDK 환경 설정과 `HANEULMARU_ORT_LIB` 지정 후:

```powershell
cargo test --release --locked --manifest-path multidevice_app/native_video/Cargo.toml
$env:HANEULMARU_NATIVE_VIDEO = (Resolve-Path 'multidevice_app/native_video/target/release/haneulmaru-video.exe').Path
python -m unittest multidevice_app.test_native_video -v
python -m multidevice_app.verify_native_video --frames 600 --width 1920 --height 1080 --paced
```

Python은 이때 영상 생성과 결과 비교용 테스트 도구다. 통합 테스트는 실제 Rust 실행 파일을 사용해 600프레임 60Hz 기준 결과, 1080p와 60fps 제한, 회전, 구간/반복, 사용자 클래스, localhost 입력 단절/복구, 재시작 예산, 추론 시작/쓰기 정지, IMU 수신/만료를 검사한다. CI는 Windows와 Ubuntu에서 SDK를 설치하고 직접 빌드한다. 일반 Python 테스트 검색에서는 `HANEULMARU_NATIVE_VIDEO`가 없으면 이 전용 테스트를 건너뛴다.

[측정 JSON](../validation/native-video-60fps-2026-11-16.json)은 128 입력 크기의 시험용 ONNX와 합성 영상 결과다. 실제 학습 모델, 카메라, 네트워크, 하드웨어에서의 60fps 보장이 아니다. `steady_output_fps`는 초기 10프레임을 제외한 출력 간격 기준이며 출력 전달과 대기를 포함한다. `host_service_ms`는 입력/추론 처리 시간이고 출력 직렬화/쓰기와 FPS 대기는 제외한다. 공유 CI에서는 속도를 합격 기준으로 사용하지 않는다.
