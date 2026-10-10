## YOLO11 + ByteTrack / BoT-SORT 코드

GPU PC에서 학습할 코드를 추가했습니다. 아직 학습된 가중치나 성능 측정 결과는 없습니다.

1. `python prepare_yolo_rgb.py` — 원본 제외, 드론 전체 박스를 R/B/G 3클래스로 변환 (630/180/90장).
2. `python train_yolo_rgb.py --device 0` — GPU 학습.
3. `python track_yolo_rgb.py --weights runs_rgb/drone_rgb/weights/best.pt --source input.mp4 --tracker bytetrack --device 0 --show`

`--tracker botsort`로 전환할 수 있습니다. 추론에는 정답 좌표가 필요하지 않습니다.
설치·실행·다른 코드와의 연동 방법: [YOLO_추적_사용법.txt](YOLO_추적_사용법.txt).

## R / B / G 색상 판별 코드 추가

`classify_rgb.py`는 표식 영역의 실제 픽셀을 보고 빨강 `R`, 파랑 `B`, 초록 `G`를 출력합니다.
`none` 원본은 제외합니다. 표식 위치는 manifest에서 읽고, 색상 정답이나 파일명은 판별에 사용하지 않습니다.
실행 방법은 [RGB_사용법.txt](RGB_사용법.txt)를 참고하세요. 이번 추가에서는 테스트를 실행하지 않았습니다.

이 저장소에는 사용자가 전달한 `photo_color_v1` 폴더를 사진과 함께 보관합니다.
아래 기존 문서의 '사진은 Git에 포함하지 않음'은 이 업로드 이전 배포 방침입니다.
출처 정보와 개별 사진 권리 확인 여부에 대한 기존 기록은 그대로 보존했습니다.

# 정지사진 drone / 색상표식 데이터셋

Roboflow의 실제 사진에 합성 색상 표식을 추가하는 **정지사진 실험용 데이터셋 제작기**입니다. 객체 클래스는 `drone` 하나이고, `red`, `blue`, `green`은 별도 속성입니다. 소속·의도를 추론하는 라벨은 없습니다.

## 팀원이 바로 확인하기

전달받은 `photo_color_v1_팀원전달.zip`을 풀고 `미리보기.html`을 엽니다. Python·GPU·Roboflow 로그인 없이 사진과 색상 변형을 확인할 수 있습니다. `test` 필터를 선택하고 사진을 클릭하면 크게 표시됩니다. 정답은 기본적으로 숨깁니다.

공개 Git에는 사진 ZIP을 재배포하지 않습니다. 원본 업로더는 CC BY 4.0을 명시하지만 실제 일부 이미지에 상용 사진 워터마크가 있어 개별 사진의 권리까지 확인하지 못했습니다. Git만 받은 팀원은 아래 원본 ZIP을 다운로드한 뒤 루트의 `데이터셋_재생성.cmd`를 실행하면 같은 데이터셋을 만들 수 있습니다. 라벨·manifest·검증 보고서는 `datasets/photo_color_v1_metadata/`에 포함합니다.

- 원본 300장, 각 원본의 빨강/파랑/초록 변형 900장: **총 1,200장**
- `labels/`: YOLO 형식의 **drone 하나**, 클래스 ID 0
- `annotations/`: COCO 형식의 **drone 하나**, 클래스 ID 1
- `marker_labels/`: 별도 표식 bbox. ID 0=red, 1=blue, 2=green
- `manifest.json`: 원본 해시, 분할, 색상 속성, 객체/표식 좌표
- `data.yaml`: drone 탐지용. 학습 도구가 상대 경로를 다른 기준으로 해석하면 `path`를 압축 해제 폴더의 절대 경로로 변경
- `none`: 합성 표식을 추가하지 않은 원본. 원래 사진의 모든 빨강/파랑/초록 픽셀이 없다는 의미가 아님

`marker_labels/`는 별도 데이터로더에서 읽는 추가 타깃이며, 일반 YOLO 학습기의 `data.yaml`만으로 자동 사용되지 않습니다. 두 라벨 폴더를 합치면 안 됩니다.

## 재생성

1. [Roboflow 원본 v1](https://universe.roboflow.com/ai-bmkoo/drone-detection-inlmy/dataset/1/download/coco)에서 **COCO → ZIP**으로 다운로드합니다.
2. Python 3.10+ 환경에 Pillow를 설치하고 아래 명령을 실행합니다. 이 제작기는 네트워크나 API 키를 사용하지 않습니다.

```powershell
python -m pip install -r requirements.txt
python build_dataset.py --source "Drone-Detection.v1i.coco.zip" --output "photo_color_v1" --count 300
python validate_dataset.py "photo_color_v1"
```

출력 폴더가 비어 있지 않으면 덮어쓰지 않습니다. 확장 버전은 다른 폴더를 지정하세요. 같은 원본 ZIP과 seed(기본 20261010), 동일 Pillow 버전에서 재현 가능합니다.

## 선정과 분할

원본에는 사진 3,485장과 bbox 4,000개가 있습니다. 사용된 annotation category는 이름 `0`, 상위 분류 `Drones`인 ID 1 하나입니다. 그 ID만 `drone`으로 바꿉니다. 다른 프로젝트의 클래스는 임의로 통합하지 않고 오류로 중단합니다.

한 사진에 객체가 한 개이며 bbox의 짧은 변이 120px 이상인 사진을 후보로 사용합니다. 원본 파일명·디코딩된 픽셀 해시 및 dHash64 거리 4 이하의 유사 사진을 제외한 뒤 300장을 선택하고 **원본 단위로 70/20/10 분할**합니다. 그 후 네 변형을 생성합니다. 세 색의 위치·크기·밝기 조건은 원본별로 같습니다.

## 한계와 검증

표식은 bbox 중심 부근에 합성한 **2D 원형 태그**입니다. 기체 표면을 분할하거나 실물 스티커를 촬영한 데이터가 아니며, 빈 공간에 걸칠 수 있습니다. 원본은 이미 620×620으로 늘린 공개 이미지입니다. 전체 사진의 라벨을 수작업으로 다시 검수하지 않았습니다.

검증기는 파일 해시, 이미지 크기, 박스 범위, 클래스, 색상 수, 원본 단위 분할 누출과 선택 원본간 dHash 거리를 확인합니다. 이는 학습 정확도 측정이 아니며, 모든 동일 영상 프레임/유사 장면 누출을 배제했다는 뜻도 아닙니다. 실제 휴대폰 재촬영 성능은 독립적인 정지사진 시험 세트로 따로 확인해야 합니다.

**학습 모델은 포함하지 않습니다.** 미리보기에는 추론 기능이 없습니다. 현재 하늘마루 렌더러의 카메라 입력과 연결하지 않았습니다.

## 출처와 이용조건

AI, *Drone-Detection*, version 1, [Roboflow Universe](https://universe.roboflow.com/ai-bmkoo/drone-detection-inlmy/dataset/1). 업로더 명시: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). 출처 표시를 유지하세요.

변경사항: 원본 일부 선정, 분할 재구성, 라벨 이름 정규화, 색상 태그 합성, COCO/YOLO 변환. 원본 및 사진 변형 ZIP은 Git에 포함하지 않습니다. ZIP 내부 `DATASET_CARD.txt`, `build_report.json`, `validation_report.json`에 출처와 제작·검증 정보가 들어 있습니다. 공개/상업적 재배포 전에 개별 원본 권리를 확인하세요.
