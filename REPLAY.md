# 가상 데이터 재생 실험실

## 범위와 구성

Unity 미사용. 기존 카메라 AR/스트리밍 코드를 바꾸지 않고 `experiments/haneulmaru_pro/replay/`에 가상 도형 재생 화면을 추가합니다. 기존 화면의 링크로 접근하거나 `/replay/`를 직접 엽니다. 새 페이지는 카메라 권한, 센서, YOLO, 객체 추적, 군사 소속 판정, 실제 위치, 장비 제어를 사용하지 않습니다.

| 파일 | 역할 |
| --- | --- |
| `core.mjs` | 시나리오 검증, 선형 보간, 미터 단위 위치와 구간 속도, 샘플 공간 분할 |
| `providers.mjs` | 같은 `list()` / `load(id)` 인터페이스의 로컬 JSON 및 HTTPS API 제공자 |
| `app.mjs` | Three.js 도형·경로·셀 표시와 재생 UI |
| `data/*.json` | 사전에 작성한 두 개의 가상 시나리오 |
| `aws-handler.mjs` | 패키지에 포함된 시나리오를 읽는 Lambda 핸들러 |
| `infra/aws/template.yaml` | JWT 인증 API Gateway HTTP API + Lambda 배포 템플릿 |

새 화면에서는 데이터 제공자 → 공통 재생 계산 → 시각화를 분리합니다. 기존 `world.js`는 유지합니다. 원격 API도 전체 시나리오 파일을 한 번 읽어 같은 로컬 재생 계산에 넣으며, 실시간 스트림이나 외부 판단 호출을 하지 않습니다.

## 실행과 조작

1. 저장소 루트에서 `python experiments/haneulmaru_pro/server.py --local --no-browser` 실행.
2. `http://127.0.0.1:8791/replay/` 열기. 외부 모델·라이브러리 다운로드 없음.
3. 재생/일시정지, 처음으로, 시간 슬라이더, 0.5/1/2배속을 선택합니다. 끝에 도달하면 멈춥니다.
4. 객체 버튼으로 해당 도형의 좌표·구간 속도를 확인합니다.
5. 공중 원점을 0~150m로 변경합니다. 상대 XYZ와 속도는 유지되고 가상 기준면 대비 높이만 바뀝니다.
6. 공간 분할 단계와 경로 표시를 변경합니다. 숨겨진 브라우저 탭에서는 재생 시간을 진행하지 않습니다.

`--local` 서버는 loopback 전용입니다. 휴대폰에서는 기존 실행기가 제공하는 HTTPS 주소 뒤에 `/replay/`를 붙입니다. 루트 `python -m http.server`만 실행하면 저장소 폴더 경로까지 URL에 포함해야 합니다.

## 좌표와 공간 표현

- 좌표: `local-xyz-y-up-m`. X/Z는 가상의 수평 축, Y는 위쪽, 길이는 미터입니다. 지도 위도·경도나 GPS/실제 해발고도가 아닙니다.
- `absolutePosition.y = originAltitude + position.y`. 기준면을 아래로 이동시키는 표시와 같은 의미입니다. 원점 변경은 물체 이동 명령이 아닙니다.
- 위치는 시간순 키프레임을 선형 보간합니다. 속도는 해당 구간의 위치 차이/시간 차이이며 물리 엔진이나 추정기가 아닙니다. 마지막 프레임의 속도 필드는 마지막 구간의 기울기이고 재생 시계는 종료 시 멈춥니다.
- Octree: 미리 지정한 큐브 안에서 최근 최대 8초의 시나리오 위치 샘플을 나눕니다. 빈 자식은 표시하지 않습니다. 관심도/위험도에 따라 자동 변경하지 않고 사용자가 단계만 선택합니다.
- 셀 밝기: `해당 셀의 샘플 수 / 전체 샘플 수`. 합은 1이며 서로 다른 객체·시각을 합친 빈도입니다. **물체 존재 확률, Bayesian occupancy, 관측 신뢰도는 구현하지 않았습니다.**
- 경로 부피: 전체 사전 작성 경로 주위에 정해진 반경으로 그린 관입니다. 아직 재생하지 않은 구간도 이미 작성된 데이터이며 미래 위치를 예측하지 않습니다.
- `demo-a`, `demo-b`, `unreviewed`는 가상 표시 그룹입니다. 외형 인식이나 민간/아군/적군 판정 결과가 아닙니다.

## API 계약 v1

`GET /scenarios` → `{ "scenarios": [{"id":"floating-shapes","title":"공중 도형 · 기본 재생","source":"synthetic"}] }`

`GET /scenarios/{id}` → 아래 구조의 시나리오 하나. 실제 전체 예제는 `replay/data/` 참조.

```json
{
  "schemaVersion": 1,
  "source": "synthetic",
  "coordinates": "local-xyz-y-up-m",
  "id": "example",
  "title": "가상 도형",
  "duration": 10,
  "originAltitude": 100,
  "bounds": {"center": [0, 20, 0], "size": 100},
  "objects": [{
    "id": "shape-1", "name": "구체 A", "shape": "sphere", "group": "demo-a", "radius": 2,
    "frames": [{"t": 0, "p": [0, 10, 0]}, {"t": 10, "p": [10, 20, 0]}]
  }]
}
```

객체 최대 24개, 객체당 최대 601프레임, 길이 최대 300초, HTTP 응답 최대 1MB입니다. ID 중복, 비유한 좌표, 순서가 뒤집힌 시간, 경계 밖 위치, 틀린 출처/좌표계는 거부합니다. `source: synthetic`은 형식 검사이며 출처의 진위를 인증하지는 않습니다. 배포하는 시나리오는 사람이 검토해야 합니다.

API 연결은 HTTPS와 액세스 토큰이 필요합니다. 토큰은 URL, localStorage, sessionStorage, 파일에 저장하지 않고, 입력 직후 입력칸을 비웁니다. 새로고침 시 기본 로컬 데이터로 돌아갑니다. 만료·네트워크 실패·잘못된 데이터는 메시지를 표시하며 현재 장면을 유지합니다. 기본 데이터로 전환하면 원격 제공자 참조를 해제합니다. 화면에 입력하는 토큰은 AWS Access Key가 아니라 해당 API용 짧은 수명의 OAuth 액세스 토큰입니다.

## 확인 방법

```powershell
cd experiments/haneulmaru_pro
npm test
python -m unittest discover -s replay -p "test_*.py"
```

Node 테스트는 기존 지형/전송 테스트와 재생·공간 분할·API 실패·Lambda 라우팅 테스트를 포함합니다. Python 테스트는 두 정적 서버의 실제 HTTP 응답과 공개 파일 범위를 확인합니다.

배포 전에는 실제 휴대폰 화면과 성능, 배포된 JWT/CORS 동작을 추가 확인해야 합니다. 특정 GPU의 FPS/VRAM 수치, 검증하지 않은 모델명/API의 지원 여부는 약속하지 않습니다.
