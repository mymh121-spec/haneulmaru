# Rust 로컬 LLM 실행 결과 분석

`haneulmaru-llm`은 `run.py --jsonl`이 저장한 결과를 읽고 한국어 요약 또는 질문 답변을 만드는 별도 Rust 실행 파일이다.
`mistralrs = "=0.8.1"` 공식 crates.io SDK를 직접 링크한다. 포크, Git 서브모듈, 복사한 추론 엔진, Python 중계, 외부 LLM API는 사용하지 않는다.
버전과 전이 의존성은 `Cargo.lock`으로 고정한다. 모델 가중치는 포함하거나 자동 다운로드하지 않는다.

## 구성

```text
기존 영상/IMU 실행 → results.jsonl
                       ↓
Rust evidence 모듈: 검증, 세션별 통계, IMU 누락, 처리시간
                       ↓
Rust CLI → 별도 Rust 추론 프로세스 → mistral.rs → 로컬 GGUF
                       ↓
근거 통계 + 질문 + 모델 답변 JSON
```

영상 경로에서는 LLM을 호출하지 않는다. 녹화 종료 후 실행하면 CPU/GPU 경합도 피할 수 있다.
별도 프로세스여도 동시에 실행하면 자원을 공유하므로 기존 60fps 측정이 유지된다고 보장하지 않는다.
기존 웹 서버, 영상 입력과 학습용 Python까지 이 변경에서 이식한 것은 아니다. **새 결과 분석 기능은 입력부터 추론과 출력까지 Rust다.**

## 빌드

저장소 루트에서 실행한다. Rust 1.97 환경에서 검증하며 Windows는 MSVC C++ 빌드 도구와 CMake가 필요하다.
첫 빌드는 모델 없이도 SDK와 네이티브 의존성 때문에 시간이 걸린다.

```sh
cargo build --release --locked --manifest-path multidevice_app/local_llm/Cargo.toml
```

실행 파일은 `multidevice_app/local_llm/target/release/haneulmaru-llm`이다. Windows에서는 `.exe`를 붙인다.
기본은 CPU다. NVIDIA용은 `--features cuda`로 빌드한 뒤 실행 시 `--accelerator`를 준다.
macOS Metal은 `--features metal`과 `--accelerator`를 사용한다. CUDA/Metal 빌드와 GPU 성능은 별도 검증 대상이다.
CPU 기본 의존성에도 upstream이 제공하는 다른 모델 계열 관련 코드가 포함된다.
LLM 빌드를 별도 Cargo 패키지로 둬 기존 영상 작업자의 빌드/실행 의존성이 증가하지 않게 했다.

## 모델 없이 근거 확인

```sh
cargo run --locked --manifest-path multidevice_app/rust/Cargo.toml -- report results.jsonl
cargo run --locked --manifest-path multidevice_app/local_llm/Cargo.toml -- --results results.jsonl --prepare-only --question "IMU가 없는 프레임은 얼마나 돼?"
```

첫 명령은 가벼운 기존 Rust 작업자로 통계만 출력한다. 두 번째는 실제 질문과 시스템 프롬프트까지 보여주지만 추론을 실행하지 않으며 `model_executed=false`, `answer=null`을 명시한다.

## 로컬 GGUF로 답변 생성

지원되는 텍스트 GGUF 파일을 사용자가 준비한 뒤 실행한다. 아래 모델 경로는 예시이며 자동 다운로드 명령이 아니다.

```sh
multidevice_app/local_llm/target/release/haneulmaru-llm --results results.jsonl --model /absolute/path/model.gguf --question "처리 지연과 IMU 누락을 요약해줘" --max-tokens 512 --timeout-secs 120 --output analysis.json
```

Windows에서는 `--model "C:\models\model.gguf"`와 `.exe` 실행 파일을 사용한다.
모델에 채팅 템플릿이 없으면 그 모델에 맞는 로컬 파일을 `--chat-template "C:\models\chat_template.jinja"` 또는 `.json`으로 지정한다.
템플릿을 임의로 다른 모델에서 가져오면 답변 품질이 달라진다. 현재 CLI는 단일 GGUF 파일을 받으며 분할 GGUF는 지원하지 않는다.
GGUF 확장자만으로 호환성을 보장하지 않는다. 모델 구조와 양자화 방식이 고정된 SDK 버전에서 지원되어야 한다.

`--question`을 바꿔 같은 기록에 다시 질문할 수 있다. 대화 이력은 유지하지 않고 매번 파일의 통계에서 답한다.
질의 범위는 색상별 관측 수, 세션별 추적 ID 수, IMU 유효 프레임, 번호 누락과 처리시간이다.
프레임별 좌표 궤적이나 개별 장면을 검색하는 기능은 아직 없으며 원본 이미지도 LLM에 보내지 않는다.

## 결과 해석과 실패 처리

- 근거는 Rust가 계산하고 답변은 모델이 생성한다. `answer_status=model_generated_not_fact_checked`이며 모델이 근거를 정확히 인용했는지는 별도 확인이 필요하다.
- 관측 수는 실제 드론 수가 아니다. 같은 ID도 세션이 다르면 별개로 센다.
- `imu_unavailable_frames`는 `valid=false` 또는 IMU 필드 없음이다. 통신 단절, 센서 고장 등의 원인을 추정하지 않는다.
- `processing`은 기록된 `processing_ms`의 통계다. p95는 nearest-rank 방식이며 60Hz 예산 초과는 `>1000/60 ms`로 센다. 카메라 FPS, 전체 지연, 실시간 마감 보장이 아니다.
- 순서가 뒤집히거나 중복된 프레임, 바뀐 세션 내 시간 기준, 잘린 JSONL은 오류로 처리한다. 손상된 행을 몰래 건너뛰지 않는다.
- 입력은 행당 1MiB, 100만 프레임, 64세션, 세션별 ID 합계 10만 개로 제한한다. 큰 기록은 나눠 분석한다. 질문은 UTF-8 4096바이트, 근거 프롬프트는 64KiB 제한이다. 모델의 실제 컨텍스트 한계는 이보다 작을 수 있다.
- 모델 로딩과 생성 전체에 기본 120초 제한을 걸고 초과 시 자식 프로세스를 종료한다. 답변은 성공한 경우에만 저장하며 기존 파일을 덮어쓰지 않는다.
- 파일의 자유문자열 표시 이름, 엔진 메시지와 세션 이름은 프롬프트에서 제외한다. 도구 실행, 셸, MCP, 검색 기능을 연결하지 않는다.
- Hub ID 대신 실제 존재하는 로컬 경로를 SDK에 전달하며 Hub 토큰을 사용하지 않는다. `HF_HUB_OFFLINE=1`도 자식 프로세스에 설정한다. 첫 의존성 빌드에는 인터넷이 필요하다.

## 테스트와 검증 경계

```sh
cargo test --locked --manifest-path multidevice_app/rust/Cargo.toml
cargo test --locked --manifest-path multidevice_app/local_llm/Cargo.toml -j 2
```

외부 모델 없이 통계, 600프레임 60Hz 기록 집계, 잘못된 입력, 프롬프트 경계, 실제 CLI 준비 모드, 로컬 GGUF 오류, 자식 프로세스 제한시간과 출력 보존을 검사한다.
추가로 Rust 테스트 코드가 작은 미학습 GGUF를 직접 생성해 실제 SDK의 로딩, 토크나이저, CPU 추론, 2토큰 생성과 결과 JSON 저장까지 검사한다. 이 모델은 `x` 토큰을 내도록 만든 실행 경로 시험용이며 질문에 답할 능력이 없다. 다운로드한 가중치나 모의 추론 응답을 사용하지 않는다.
`sleeping_child`의 ignored 표시는 제한시간 검사에서 자식 프로세스로만 실행하는 보조 테스트다.
학습된 GGUF가 없으므로 실제 한국어 답변 품질, 실제 모델의 메모리 사용량과 tokens/s는 미검증이다.

## 의존성 출처

- [mistral.rs 원본](https://github.com/EricLBuehler/mistral.rs), [고정 패키지 0.8.1](https://crates.io/crates/mistralrs/0.8.1), MIT 라이선스.
- [공식 GGUF 로컬 실행 예제](https://github.com/EricLBuehler/mistral.rs/blob/v0.8.1/mistralrs/examples/getting_started/gguf_locally/main.rs).
- 엔진 라이선스와 모델 가중치 라이선스는 별개다. 모델은 저장소에 추가하지 않는다.
- `smallvec 2.0.0-beta.4`는 현재 Rust와 호환되는 lockfile 선택이다. beta.5는 Rust 1.100을 요구하므로 무분별한 lockfile 갱신에 주의한다.
