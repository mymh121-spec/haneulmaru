# upv1 — wan_V1 경량 정리본

`upv1.zip`은 최적화한 소스 코드 102개 파일을 담은 오프라인 묶음입니다. 압축을 풀면 `wan_V1` 폴더가 나옵니다.

[최적화 묶음 내려받기](upv1.zip)

## 변경 내용

- 학습 사진·라벨·미리보기와 디자인 자료를 실행 묶음에서 분리했습니다.
- 완료된 1회성 GitHub 데이터 복원 workflow를 제외했습니다.
- NU40 수신 코드와 방향 학습 모델을 공통 경로로 통일했습니다.
- 외부 화면만 볼 때는 JSON 문자열 변환을 생략합니다.
- JSONL 파일 flush는 프레임 처리 중 0.5초 간격으로 묶습니다. `--flush-every-frame`을 지정하면 매 프레임 flush합니다.
- ClikaRT 전처리 버퍼를 재사용하고 박스 복원을 배열 연산으로 처리합니다.
- 기존 UI·좌표 코드·YOLO 학습/추적 도구·Android 인터페이스·vendor 라이선스·참고 펌웨어·기존 테스트를 유지했습니다.

최적화 ZIP은 487,652바이트(약 0.49MB)이며 이전 전체 ZIP 약 61MB에서 99.2% 줄었습니다. 이는 배포 용량 감소이며 실행 속도 측정 결과가 아닙니다.

## 실행과 학습

압축 안의 `wan_V1/README.md`, `wan_V1/정리내역.txt`, `wan_V1/multidevice_app/구조와실행.txt`를 참고하세요. 실행 파일의 Python 의존성은 미리 설치해야 합니다.

NU40는 USB를 우선 사용하고 BLE 연결을 유지하며 기존에 학습한 상하좌우 방향을 사용합니다. S22 실시간 입력에는 별도 카메라 송신기가 필요합니다. ClikaRT SDK·라이선스·학습된 모델 및 Android APK는 포함되지 않았습니다.

학습 사진·라벨은 [정리 전 GitHub 자료](https://github.com/mymh121-spec/haneulmaru/tree/7c81fe5173318f6667d786457434dffdfd3d1b05/photo_color_v1)에 보존되어 있습니다. 로컬에서 전달받은 `wan_V1_original.zip`을 `wan_V1` 폴더 옆에 두면 `python photo_color_v1/restore_training_data.py`로 복원할 수 있습니다. GitHub 원본 ZIP을 사용한다면 압축을 풀고 `photo_color_v1`의 images, labels, marker_labels, annotations 폴더와 미리보기 파일을 같은 경로로 복사하세요.

## 확인 범위

이번 정리에서는 실행 테스트, 장치 연결, 실제 ClikaRT 추론, Android 빌드와 성능 측정을 수행하지 않았습니다. 포함된 과거 검사 기록은 이번 최적화의 실행 검증 결과가 아닙니다.

## 파일 확인

- 기준 원본 커밋: `7c81fe5173318f6667d786457434dffdfd3d1b05`
- ZIP SHA-256: `6db22a39b88c4d3993978c732f6fd0ed5fcb29dc9440376d0f108a6e2d7e98b3`
- 압축 내부 파일 체크섬: `wan_V1/FILES_SHA256.json`
