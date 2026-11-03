# 새 객체 탐지 데이터셋과 모델 사용하기

기본 데모는 R/B/G 드론 모델을 사용합니다. 새 데이터셋마다 클래스 ID, 학습한 가중치와 모델 입출력 설정을 하나의 `profile.json`에 보관합니다. 데이터셋을 가져오면 학습용 `data.yaml`과 모델 프로필이 함께 생성됩니다. 데이터셋별로 다른 출력 폴더를 사용하세요.

아래 명령은 압축을 푼 저장소의 최상위 폴더에서 실행합니다. Windows에서 기존 영상 앱의 가상환경을 사용하는 경우 `python`은 `multidevice_app\.venv\Scripts\python.exe`로 바꾸면 됩니다.

## 1. 데이터 가져오기

```powershell
python -m pip install -r multidevice_app/requirements.txt

# 동봉된 단일 클래스 drone 데이터셋으로도 실행할 수 있습니다.
python -m multidevice_app.datasets yolo --data photo_color_v1/data.yaml --output imported/drone

# 다른 COCO 탐지 데이터셋 예시. 각 JSON 다음 인자는 file_name이 시작되는 이미지 폴더입니다.
python -m multidevice_app.datasets coco --train incoming/train.json incoming/train_images --val incoming/val.json incoming/val_images --output imported/site
```

| 입력 | 처리 규칙 |
|---|---|
| YOLO Detect | `data.yaml`에 `train`, `val`, 선택적 `test`, `names`를 지정합니다. 이름 목록과 0부터 연속인 ID 사전을 모두 지원합니다. `path`는 YAML 위치 기준입니다. 분할에는 이미지 폴더, 개별 이미지, 이미지 목록 TXT 또는 이들의 목록을 사용할 수 있습니다. TXT의 상대 경로는 TXT 위치 기준입니다. 표준 `images/...`와 `labels/...` 구조 또는 이미지 옆의 동명 TXT를 읽습니다. 라벨 한 줄은 `class_id cx cy width height`이고 좌표는 0~1입니다. 누락되거나 빈 라벨은 배경 이미지로 처리합니다. |
| COCO Instances | 각 분할의 `images`, `categories`, `annotations`를 읽습니다. 학습 분할의 category ID를 정렬해 연속 ID로 바꾸고, 다른 분할은 이름으로 같은 클래스에 연결합니다. 픽셀 `[x,y,width,height]`를 영상 경계에 맞춰 YOLO 좌표로 변환합니다. `iscrowd` 박스는 생략합니다. 빈 이미지도 가져오며 마스크·키포인트는 사용하지 않습니다. |
| 공통 출력 | 이미지 복사본, 분할별 YOLO 라벨, `data.yaml`, `profile.json`이 새 폴더에 생성됩니다. 잘못된 클래스 ID, 잘못된 박스, 같은 이미지 경로의 중복 사용, 학습 박스가 없는 데이터셋을 거부합니다. `UNKNOWN`은 파이프라인이 낮은 신뢰도의 결과에 사용하는 예약어입니다. |

**다른 형식을 추가할 때**는 `multidevice_app/datasets.py`의 두 리더처럼 클래스 목록과 분할별 `Sample(image, boxes)`를 반환하는 리더를 연결합니다. `boxes`는 연속 클래스 ID와 정규화한 중심 x/y, 너비, 높이입니다. 같은 `write_dataset`이 출력과 공통 검사를 담당합니다.

### API에서 데이터 받기

원격 데이터는 가져오기 명령을 실행할 때 내려받습니다. 결과 폴더에 같은 `data.yaml`과 `profile.json`이 생성되므로 학습·Rust 추적 명령은 로컬 데이터와 같습니다. `source.json`에는 제공자, 확인된 리비전, 클래스 ID 매핑, 분할별 이미지·박스 수, 적용한 샘플 제한이 남습니다.

**Hugging Face.** 동봉된 예시는 공개 양 탐지 데이터셋에서 `train`과 `validation`을 가져옵니다. 먼저 각 분할에서 3장만 받아 검사할 수 있습니다.

```powershell
python -m multidevice_app.datasets remote --source multidevice_app/examples/hf-sheep.json --output imported/sheep-sample --limit-per-split 3
```

전체 분할을 가져올 때는 `--limit-per-split`을 빼고 새 출력 폴더를 지정하세요. 다른 Hub 데이터셋은 예시 JSON을 복사해 `dataset`, `config`, `splits`, `classes`, `fields`, `bbox_format`을 변경합니다. Dataset Viewer의 `/rows`에 게시된 데이터셋을 지원하며 페이지당 1~100행을 요청합니다. `ClassLabel` 메타데이터가 있으면 설정된 ID/이름과 비교합니다. 모든 페이지의 `X-Revision`이 같아야 하며 `revision`을 설정하면 해당 값과도 일치해야 합니다. 이 옵션은 현재 제공 중인 버전을 확인하는 용도이며 과거 버전을 내려받는 기능은 아닙니다. [Hugging Face API 문서](https://huggingface.co/docs/dataset-viewer/rows) · [예시 원본 데이터](https://huggingface.co/datasets/keremberke/aerial-sheep-object-detection)

**일반 HTTP API.** [HTTP 설정 예시](examples/http-detections.json)를 복사해 `base_url`, 분할별 첫 페이지 경로와 행 필드를 실제 서비스에 맞춥니다. 예시의 주소는 자리표시자입니다.

```powershell
python -m multidevice_app.datasets remote --source my-api.json --output imported/my-dataset
```

기본 페이지 응답 예시는 다음과 같습니다. 마지막 페이지에는 `"next": null`을 넣습니다. 다음 URL은 상대 또는 절대 경로를 사용할 수 있습니다.

```json
{
  "items": [
    {
      "id": "sample-17",
      "image_url": "https://images.example.org/17.png",
      "annotations": [{"box": [10, 20, 90, 70], "category_id": 70}]
    }
  ],
  "next": "?split=train&after=17"
}
```

| 설정 항목 | 의미 |
|---|---|
| `splits` | 로컬 `train`, `val`, 선택적 `test`를 서로 다른 원격 분할 이름(Hub) 또는 첫 페이지 경로(HTTP)에 연결합니다. |
| `fields` | 행 ID, 이미지, 박스 배열, 라벨 배열을 지정합니다. Hub에서는 ID 경로를 생략하면 해당 분할의 Viewer 행 번호를 씁니다. `annotations.box`처럼 중첩된 객체 목록을 읽을 수 있습니다. 이미지 값은 URL 문자열 또는 `src`가 있는 객체입니다. 빈 박스·라벨 배열은 배경 이미지로 처리합니다. |
| `classes`, `category_ids` | 클래스 이름을 YOLO 순서대로 나열합니다. 원격 정수 ID가 5, 70처럼 불연속이면 같은 순서로 `category_ids`를 지정합니다. 라벨 값이 클래스 이름 문자열인 API도 지원합니다. |
| `bbox_format` | 픽셀 단위의 좌상단·너비·높이는 `xywh_pixels`, 양 모서리는 `xyxy_pixels`, 정규화된 중심·너비·높이는 `cxcywh_normalized`입니다. 다운로드한 이미지 크기를 기준으로 경계를 자르고 빈 박스는 거부합니다. 주석 기준 크기를 제공하면 `fields.width`와 `fields.height`를 함께 설정하세요. 실제 크기가 다르면 해당 행에서 중단합니다. |
| `pagination` | HTTP 응답의 배열·이어서 읽을 값의 필드명을 바꿀 수 있습니다. 예를 들어 `{"items":"data.samples","next":"links.next_cursor","cursor_param":"after"}`로 설정하면 커서 문자열을 다음 요청의 `after` 쿼리로 보냅니다. `cursor_param`을 생략하면 다음 값은 URL입니다. |
| `token_env`, `asset_origins` | 인증이 필요한 경우 환경변수 **이름**을 지정합니다. Bearer 토큰은 API와 같은 프로토콜·호스트·포트에만 보냅니다. 이미지를 다른 호스트에서 내려받으려면 해당 origin을 명시하세요. 그 호스트에는 API 토큰을 보내지 않으므로 공개 이미지 또는 서명된 URL을 사용해야 합니다. |

API와 이미지 다운로드에는 HTTPS를 사용합니다. 로컬 시험용 루프백 주소는 HTTP도 허용합니다. 연결 오류와 일부 일시적 HTTP 오류는 최대 3회 시도합니다. 중복 페이지·행 ID, 동일 이미지 바이트, 잘린 Hub 응답, 리비전 변경, 잘못된 이미지·박스가 발견되면 최종 폴더를 게시하지 않습니다. 오류 원인을 바로잡은 후 처음부터 다시 실행합니다. HTTP 서버가 `X-Revision`을 제공하지 않으면 출력에 리비전을 `null`로 기록합니다. `source.json`에는 토큰이나 이미지의 서명 URL을 저장하지 않습니다.

## 2. 학습하고 ONNX로 내보내기

학습은 별도 단계입니다. Ultralytics 학습 환경에서 생성한 YAML로 탐지 모델을 학습합니다. 아래는 YOLO11 탐지 모델 예시입니다.

```powershell
yolo detect train model=yolo11n.pt data=imported/site/data.yaml imgsz=640
yolo export model=runs/detect/train/weights/best.pt format=onnx imgsz=640 batch=1 dynamic=False half=False nms=False opset=13
```

학습 실행이 출력한 실제 `best.pt` 경로를 사용하세요. 내보낸 `best.onnx`를 `imported/site/profile.json` 옆에 놓거나 프로필의 `model_path`를 바꿉니다. 이미 학습된 모델은 데이터를 다시 복사하거나 학습할 필요 없이 프로필로 바로 등록할 수 있습니다. 클래스 이름은 모델 학습 당시 ID 순서와 반드시 일치해야 합니다. 출력 너비로 클래스 이름의 의미까지 검증할 수는 없습니다.

## 3. Rust에서 추적하기

```powershell
python multidevice_app/run.py --profile imported/site/profile.json --source recording.mp4 --recording --show
```

`--profile`은 모델 설정만 선택합니다. 센서·영상·추적 설정은 기존 `--config`를 따릅니다. 프로필 적용 후 `--model`로 가중치를 바꿀 수 있습니다. 상대 모델 경로는 프로필 폴더 기준입니다. `--jsonl results.jsonl`을 추가하면 해당 클래스 이름과 추적 ID가 저장됩니다. 한국어 글꼴이 없는 PC에서는 `HANEULMARU_FONT`에 TrueType/OpenType 글꼴 경로를 지정할 수 있습니다.

동봉된 합성 영상으로 한글 클래스 프로필을 바로 시험할 수 있습니다.

```powershell
python multidevice_app/run.py --config multidevice_app/demo/config.json --profile multidevice_app/demo/profile.json --source multidevice_app/demo/multi-tracking.avi --recording --show
```

### Rust가 읽는 ONNX 출력

프로필에 `input_size`, 입력·출력 텐서 이름, 클래스 ID 순서와 `output_layout`을 지정합니다. 입력은 정사각형 RGB FP32 NCHW이며 중앙 letterbox 후 0~1로 정규화합니다. 출력도 FP32이고 좌표는 letterbox한 입력 영상의 픽셀 단위입니다. `C`는 클래스 수, `N`은 후보 수입니다.

| `output_layout` | 모양 | 한 후보의 값 |
|---|---|---|
| `yolo_raw` | `[1,4+C,N]` | 중심 x/y, 너비, 높이와 클래스별 확률 |
| `yolo_raw_rows` | `[1,N,4+C]` | 위와 같은 값을 행 단위로 출력 |
| `yolo_objectness_rows` | `[1,N,5+C]` | 중심 x/y, 너비, 높이, 객체 확률과 클래스별 확률. 두 확률의 곱을 점수로 사용 |
| `xyxy_score_class` | `[1,N,6]` | 후처리된 x1, y1, x2, y2, 점수, 클래스 ID. 모델이 중복 제거를 이미 수행한 출력용 |

일반 프로필에서는 같은 클래스 안에서 겹치는 박스를 제거합니다. `agnostic_nms: true`이면 서로 다른 클래스의 박스도 비교합니다. 기존 R/B/G 프로필은 이전 중복 제거 방식을 유지합니다. 다른 전처리나 다중 출력 텐서, 분할·포즈 모델에는 별도 디코더가 필요합니다.

형식 참고 자료는 [Ultralytics 탐지 데이터셋](https://docs.ultralytics.com/datasets/detect/), [모델 내보내기](https://docs.ultralytics.com/modes/export/), [COCO 데이터 형식](https://cocodataset.org/#format-data)에 있습니다.
