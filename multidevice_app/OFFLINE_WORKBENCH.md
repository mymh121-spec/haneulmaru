# 하드웨어 없는 개발 작업

저장소 루트에서 실행합니다. 설치와 CPU 런타임 설정은 [회귀 검사 안내](HARDWARE_FREE_TESTING.md)를 따릅니다.
아래 출력은 모두 새 파일/폴더를 사용하며 기존 결과를 덮어쓰지 않습니다.

## 1. 영상, IMU 재생과 탐지 캐시

```sh
python -m multidevice_app.run --config multidevice_app/demo/config.json --source multidevice_app/demo/multi-tracking.avi --recording --imu-log multidevice_app/demo/imu-replay.jsonl --coordinates multidevice_app/demo/projection.json --cache-detections detections.jsonl --jsonl results.jsonl --flush-every 16
python -m multidevice_app.run --config multidevice_app/demo/config.json --source multidevice_app/demo/multi-tracking.avi --recording --no-imu --detections detections.jsonl --start-ms 500 --end-ms 1000 --repeat 2 --show --speed 0.5
```

- 구간은 시작 포함, 끝 제외입니다. 매 반복마다 새 추적 세션을 발급합니다. Space로 정지/재개, Q로 종료합니다.
- `--show` 없이 실행하면 배속에 관계없이 최대 처리 속도로 동작합니다.
- IMU JSONL 한 줄: `{"time_ms":0,"available_ms":0,"session":"take-1","sample":{"t_ms":0,"gx":0,"gy":10,"gz":0}}`.
  각속도 단위는 degrees/second입니다. `time_ms`는 영상 시간축으로 정렬한 측정 시각,
  선택 `available_ms`는 전달 완료 시각입니다. `--imu-offset-ms`는 두 시각에 더할 영상 오프셋입니다.
  미래/350ms 초과 샘플은 사용하지 않으며 늦게 도착한 과거 샘플이 최신 값을 덮지 않습니다.
- 동봉 IMU는 소프트웨어 시험용 합성값입니다. 실제 영상/센서의 노출 시각 동기화를 보장하지 않습니다.
- 캐시는 원시 탐지 결과를 저장하여 추적 설정 비교 시 모델 추론을 생략합니다. 프레임 번호, 시각, 크기와
  디코딩한 BGR 픽셀 SHA-256을 대조하므로 영상이나 디코더 결과가 달라지면 재생을 거부합니다.
  모델 해시와 표시 별칭도 유지합니다. 캐시 생성은 반복/복구 없이 1회 실행합니다.
- IMU는 최초 정렬 후 이진 탐색하고, 탐지 캐시는 한 번만 읽습니다. JSONL `--flush-every`는 파일 출력 flush를
  묶습니다. 큰 값은 비정상 종료 때 마지막 버퍼를 잃을 수 있습니다. 기본값은 1입니다.
- 파이프 응답은 버퍼 읽기를 사용하고 응답 크기 상한을 둡니다. 추론/추적 기본 1MiB, 영상 64MiB입니다.

## 2. 탐지, 추적, 지연 비교

```sh
python -m multidevice_app.offline evaluate --reference multidevice_app/demo/expected-tracks.jsonl --predictions results.jsonl --warmup 3 --output evaluation.json
python -m multidevice_app.offline evaluate --reference reference.jsonl --predictions model-a.jsonl model-b.jsonl --output comparison.json
```

정답 한 줄에는 `frame_sequence`(또는 `frame`), `tracks`를 넣습니다. 각 관측은
`code`, `xyxy:[x1,y1,x2,y2]`, 선택 `id`를 사용합니다. 빈 프레임도 `tracks:[]`로 포함합니다.
반복 결과를 평가할 때 정답에도 `replay_pass`를 일치시킵니다. 양쪽에 시각/영상 크기가 있으면 서로 대조합니다.
단일 IoU 임계값(기본 .5)에서 클래스별 최적 할당으로 TP/FP/FN, precision/recall, 평균 IoU,
ID 변경과 ID 미부여를 계산합니다. 빠진 프레임과 추가 프레임도 누락/오탐에 반영합니다.
이는 COCO mAP, IDF1, HOTA가 아닙니다. 실제 인식률에는 사람이 검수한 실제 영상 정답이 필요합니다.

지연은 inference/processing의 평균, p50, p95를 제공합니다. `--warmup`은 지연 집계에서만 앞 프레임을 제외합니다.
processing에는 영상 읽기/화면/JSONL 쓰기가 포함되지 않고 `input_read_ms`가 별도입니다.
캐시 실행의 inference는 캐시 조회/픽셀 검증 비용이며 모델 추론 성능으로 해석하지 않습니다.

## 3. 입력/작업자 장애 복구

실행 명령에 `--max-restarts 3 --io-timeout 5`를 추가하면 라이브 영상 열기/읽기 및 작업자 실패를 제한된
공통 예산 안에서 복구합니다. 지수형 대기 후 작업자를 새로 만들고 `session_id`를 바꿉니다.
`recovery_count`로 횟수를 확인합니다. 기본 재시도는 0회입니다.
라이브 디코딩은 별도 프로세스이며 막힌 OpenCV 호출도 종료할 수 있습니다.
녹화 파일은 복사 비용을 줄이기 위해 직접 읽고, `--isolate-input`으로 동일 격리를 선택할 수 있습니다.
격리 모드는 BGR/base64 전달 비용이 있으므로 처리 속도 향상을 의미하지 않습니다.
한 프레임만 요청/처리하며 앱 내부에 무제한 대기열을 만들지 않습니다. 기기/스트림 자체 버퍼는 별도입니다.

## 4. 데이터 품질과 분할 유출

```sh
python -m multidevice_app.offline audit --data dataset/data.yaml --groups groups.json --output audit.json
```

`groups.json`은 선택 항목으로 data.yaml 기준 상대 이미지 경로를 원본 영상/촬영 묶음 ID로 연결합니다.
예: `{"images/train/a.jpg":"clip-01","images/val/b.jpg":"clip-01"}`.
동일 그룹이 여러 split에 있으면 `group_leakage`에 표시합니다. 검사 결과에는 클래스별 박스/배경 수,
누락 클래스, 디코딩 RGB 픽셀 중복, dHash 유사 후보가 포함됩니다.
유사 후보는 기본 해밍 거리 4, 최대 비교 100000회/후보 1000쌍이며 제한 도달은 `search_truncated`에 표시합니다.
유사 이미지에는 오탐이 있어 자동 삭제하지 않습니다. 그룹 정보 없이 원본 촬영 분할 유출을 판정하지 않습니다.
기존 데이터 가져오기는 압축/메타데이터만 다른 동일 픽셀 이미지도 거부하며 실패한 임시 출력을 정리합니다.
가져오기 결과에는 `training-classes.json`, 클래스 분포, 픽셀 해시, 출력 경로를 남깁니다.

## 5. 모델 배포 번들

```sh
python -m multidevice_app.offline bundle --profile profile.json --training-classes training-classes.json --dataset-report audit.json --evaluation evaluation.json --output release
python -m multidevice_app.offline verify-bundle release
python -m multidevice_app.run --profile release/profile.json --source recording.avi --recording --no-imu
```

가중치, 상대 경로 프로필, 학습 클래스 순서와 선택 데이터/평가 증빙을 복사하고 각 파일 SHA-256을 기록합니다.
학습 스크립트도 완료 후 실제 best 모델의 클래스 순서를 가중치 옆에 기록합니다.
클래스 순서 또는 평가의 모델 해시가 다르면 번들 생성을 거부합니다. 여러 모델 비교 보고서는 단일 모델
평가로 나눠 넣습니다. 해시 없는 평가 기록은 `evaluation_model_match=unverified`로 표시합니다.
번들을 이동한 뒤에도 검증/실행할 수 있습니다. 무결성 확인은 모델 품질이나 출처 인증을 뜻하지 않습니다.

## 6. 로컬 검토 화면과 방향 좌표

```sh
python -m multidevice_app.offline report --input results.jsonl --output review.html
```

생성 HTML을 브라우저로 열면 프레임 이동, 배속/구간 반복, 클래스 필터, ID 궤적, IMU/복구/모델 상태를 확인합니다.
영상 없이도 박스를 표시하며 파일 선택으로 브라우저가 지원하는 동일 원본 영상을 겹칠 수 있습니다.
브라우저가 AVI 코덱을 지원하지 않으면 박스 검토만 사용하세요. 영상은 업로드하지 않습니다.
대용량 기록은 구간을 나눠 출력하세요. 검토 HTML은 해당 JSONL 전체를 메모리에 담습니다.

`--coordinates projection.json`은 기존 `photo_color_v1`의 방향 관측 모듈을 연결합니다.
동봉 FOV 설정은 미보정 임시값입니다. 실내/실외의 실제 북쪽, 거리, 3D 위치 정확도를 입증하지 않으며
`range_m`은 계속 null입니다. IMU 만료/세션 변경과 자이로 누적 불확실성도 그대로 표시합니다.

## 회귀 검사

전체 Python 검사에는 실제 CLI의 48프레임/177관측 대조, 센서 재생, 캐시 구간 반복, 디코더 픽셀 일치,
시작/읽기 무응답 종료, 복구 예산, 평가 누락/오탐/ID 변경, 재압축 중복, 배포 이동/변조,
HTML 삽입 방지가 포함됩니다. CI는 Windows와 Ubuntu에서 실행합니다.
`node multidevice_app/test_review_ui.js`는 가짜 DOM으로 프레임 이동, 필터, ID/세션, 배속, 정지, 반복 제어를 검사합니다.
이는 브라우저 렌더링/실제 영상 코덱 검증이 아닙니다.
실제 장치 연결, GPU 재학습, 실제 영상 인식률 및 보정 정확도는 이 테스트의 검증 범위 밖입니다.
