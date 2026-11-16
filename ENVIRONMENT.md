# 필요한 프로그램과 실행 자료

2026-11-16 기준 저장소의 의존성 파일과 검증 기록에 맞춘 목록이다.
로컬 LLM만 실행할 때와 기존 영상·IMU 기능까지 개발할 때의 준비물이 다르다.

## 1. Rust 로컬 LLM 개발·실행

| 항목 | 필요 여부 | 용도와 기준 |
|---|---|---|
| Git | 소스 관리에 필요 | 저장소 받기, 업데이트, 변경 관리. ZIP으로 소스만 받으면 생략 가능 |
| Rust와 Cargo | 소스 빌드에 필수 | 로컬 검증 버전은 Rust 1.97.0. `Cargo.lock`을 유지하고 `--locked`로 빌드 |
| Windows C++ 빌드 도구 | Windows 소스 빌드에 필요 | Visual Studio Build Tools의 C++ 데스크톱 개발 구성, MSVC와 Windows SDK. 로컬은 VS 2022 도구로 검증 |
| CMake | 네이티브 의존성 빌드에 필요 | mistral.rs 하위 의존성 빌드. Windows는 Visual Studio의 CMake 도구 구성도 사용 가능 |
| mistralrs 0.8.1 | 필수, Cargo가 설치 | 별도 포크 없이 공식 Rust 패키지를 직접 사용. 수동 소스 복사 불필요 |
| 결과 JSONL | 분석 입력으로 필수 | 기존 영상 실행의 `--jsonl` 결과 파일 |
| 지원되는 로컬 GGUF | 실제 답변 생성에 필수 | 현재 CLI는 단일 파일 지원. 모델 구조와 양자화 방식의 SDK 호환 확인 필요 |
| 모델에 맞는 채팅 템플릿 | 모델에 따라 필요 | GGUF에 없으면 로컬 `.jinja` 또는 `.json` 파일 지정 |

Linux에서 소스를 빌드하려면 Rust 외에 C/C++ 컴파일러, 링커, CMake 등 네이티브 빌드 도구가 필요하다.
Python, Node.js, ONNX Runtime은 **이 로컬 LLM 기능 자체의 실행 의존성이 아니다.**
Cargo가 설치하는 정확한 Rust 의존성은 [Cargo.toml](multidevice_app/local_llm/Cargo.toml)과 [Cargo.lock](multidevice_app/local_llm/Cargo.lock)에 기록되어 있다.

```powershell
git --version
rustc --version
cargo --version
cmake --version
cargo build --release --locked --manifest-path multidevice_app/local_llm/Cargo.toml
```

`cmake`가 일반 터미널에서 검색되지 않아도 Visual Studio 개발자 터미널이나 도구 설치 경로에 있을 수 있다.
실행 명령과 모델 준비 모드는 [로컬 LLM 안내](multidevice_app/local_llm/README.md)를 따른다.
가중치는 자동 다운로드하지 않는다. 실제 모델이 없어도 `--prepare-only`와 시험용 GGUF 테스트는 가능하다.

## 2. 기존 영상·IMU와 웹 기능까지 사용하는 경우

| 항목 | 필요한 기능 | 현재 기준 |
|---|---|---|
| Python과 venv/pip | 영상 입력, 실행 관리, 데이터 도구, 기존 웹 서버 | Python 3.11은 Windows/Ubuntu CI 검증, 로컬은 3.10.8 검증 |
| NumPy, OpenCV, PyYAML, Pillow, requests | 영상·데이터 도구 | [multidevice_app/requirements.txt](multidevice_app/requirements.txt)로 설치 |
| ONNX Runtime CPU 네이티브 라이브러리 | Rust 영상 추론 | 회귀 검사는 `onnxruntime==1.23.2`. Rust가 DLL/SO를 직접 로드 |
| Ultralytics와 lap | Python YOLO 추론·추적, 학습 | 선택 항목. `ultralytics==8.4.63`, `lap>=0.5.12,<0.6` |
| reportlab | 기존 웹 데모 관련 Python 도구 | 루트 [requirements.txt](requirements.txt)의 `>=4,<5` |
| Node.js | 리뷰 화면 JavaScript 회귀 검사 | `node multidevice_app/test_review_ui.js`. Rust LLM 실행에는 불필요 |
| WebGL/WebRTC 지원 브라우저 | 웹 데모와 리뷰 화면 | PC와 휴대폰 화면 확인용 |
| cloudflared | 기존 외부 HTTPS 터널 실행 경로 | 실행기가 공식 배포본을 받아 해시 검증. 로컬 LLM에는 불필요 |
| 영상 파일·ONNX 가중치 | 영상 재생·추론 | 합성 시험 자료는 동봉. 실제 드론 인식에는 학습된 모델 필요 |
| 카메라·S22·NU40 | 실제 장치 연동 | 소프트웨어 회귀 검사와 녹화 분석에는 불필요 |

영상 CPU 테스트 환경의 설치 예시:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r multidevice_app/requirements.txt onnxruntime==1.23.2
```

Windows의 ONNX Runtime 경로 설정 예시:

```powershell
$env:HANEULMARU_ORT_LIB = (Resolve-Path '.venv/Lib/site-packages/onnxruntime/capi/onnxruntime.dll').Path
```

Python YOLO 추론을 선택하면 [requirements-inference.txt](multidevice_app/requirements-inference.txt), 학습은 [requirements-yolo.txt](photo_color_v1/requirements-yolo.txt)를 추가로 사용한다.
전체 검사 명령은 [하드웨어 없는 검증 안내](multidevice_app/HARDWARE_FREE_TESTING.md)에 있다.

## 3. Docker와 GPU는 선택 항목

| 항목 | 필요한 경우 | 현재 적용 상태 |
|---|---|---|
| Docker Desktop 또는 Docker Engine | 개발·배포 환경을 컨테이너로 공유할 때 | 필수 아님. 현재 Dockerfile, Compose 파일과 컨테이너 검증은 없음 |
| WSL 2 | Windows에서 Docker Desktop의 WSL 2 백엔드를 사용할 때 | Windows 네이티브 Rust 실행에는 불필요 |
| Docker Compose | 여러 컨테이너를 함께 관리할 때 | 현재의 단일 배치 분석 CLI에는 필수 아님 |
| NVIDIA GPU와 호환 드라이버, CUDA 빌드 환경 | Rust LLM의 CUDA 가속을 사용할 때 | `--features cuda` 빌드와 `--accelerator` 실행. 현재 CPU 경로만 검증 |
| NVIDIA 컨테이너 GPU 지원 구성 | Docker 안에서 NVIDIA GPU를 사용할 때 | 호스트 환경에 맞게 별도 준비. CPU 컨테이너에는 불필요 |
| Apple Metal 개발 환경 | macOS 네이티브 Metal 가속 | `--features metal`과 `--accelerator`. 현재 미검증 |

Windows Docker Desktop의 Linux 컨테이너 GPU 전달은 WSL 2 백엔드의 NVIDIA GPU 지원 경로를 따른다.
출처: [Docker Windows 설치](https://docs.docker.com/desktop/setup/install/windows-install/), [WSL 2 백엔드](https://docs.docker.com/desktop/features/wsl/), [GPU 지원](https://docs.docker.com/desktop/features/gpu/).

컨테이너화를 진행할 때는 Rust/C++/CMake를 빌드 단계에 두고, 실행 단계에는 Linux 실행 파일과 필요한 런타임 라이브러리를 넣는 구성이 적합하다.
GGUF와 결과 JSONL은 읽기 전용 볼륨, 답변 저장 폴더는 쓰기 볼륨으로 분리한다.
모델을 이미지에 포함하지 않으면 이미지 재빌드 없이 교체할 수 있다.
현재 LLM은 웹 서버가 아닌 CLI이므로 포트 공개가 필요하지 않다.
카메라, USB, IMU UDP까지 컨테이너에 연결하는 작업은 별도 장치·네트워크 검증 대상이다.

## 4. 메모리와 저장 공간

RAM과 VRAM 요구량은 사용할 GGUF의 모델 크기, 양자화, 컨텍스트 길이를 정한 뒤 산정한다.
가중치 파일 크기만큼의 메모리만 있으면 충분한 것은 아니며 KV 캐시와 연산 버퍼의 여유가 필요하다.
현재 학습된 GGUF가 없어 특정 모델의 최소 RAM·VRAM이나 초당 토큰 수를 보장하지 않는다.
디스크에는 모델 파일 외에 Rust 빌드 캐시, 녹화 영상, 결과 JSONL 공간도 확보한다.

지금 로컬 LLM을 개발하는 데 우선 준비할 것은 **Git, Rust/Cargo, OS별 네이티브 빌드 도구, 결과 JSONL, 지원 GGUF**다.
Docker, GPU, Python 영상·학습 도구는 사용할 기능에 따라 추가한다.
