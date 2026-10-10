# AWS 시나리오 API 확장점

현재 구현은 **사전 작성한 가상 시나리오를 읽는 API**입니다. 영상·이미지·센서 업로드, 탐지/추적, 실시간 제어, AI 판정, Bedrock 호출은 포함하지 않습니다. AWS 계정 없이 로컬 화면 전체를 사용할 수 있습니다.

## 구조

브라우저의 `createApiProvider` → API Gateway HTTP API의 JWT authorizer → Lambda → 배포 패키지의 JSON 시나리오.

- API: `GET /scenarios`, `GET /scenarios/{id}` 두 개만 제공합니다.
- 인증: 기존 Cognito/OIDC issuer, audience, 읽기 scope를 필수로 지정합니다. API Gateway가 JWT를 검증합니다. Lambda 자체를 공개 Function URL로 노출하지 않습니다.
- 권한: Lambda는 기본 로그 기록 권한만 사용합니다. S3, DynamoDB, Bedrock 권한 없음.
- CORS: 지정한 HTTPS 브라우저 origin 하나만 허용합니다. CORS는 인증을 대신하지 않습니다.
- 제한: 128MB 메모리, 5초 제한, 예약 동시 실행 2, API 초당 5/버스트 10, 로그 보존 7일. 트래픽 제한은 요금 상한이 아닙니다.
- 배포한 리소스 사용에는 AWS 비용이 발생할 수 있습니다. 이 변경은 리소스를 생성하거나 클라우드에 배포하지 않습니다.

## 배포 준비

AWS CLI와 SAM CLI, 배포 권한이 있는 AWS 계정, HTTPS로 호스팅된 재생 화면, 기존 Cognito 또는 OIDC 앱이 필요합니다. OAuth는 브라우저용 Authorization Code + PKCE를 사용하도록 구성하고, 앱에 client secret을 넣지 마세요. 이 데모는 로그인 화면을 구현하지 않고 발급된 **access token**을 받는 입력칸을 제공합니다.

인증 공급자에 `haneulmaru/scenarios.read` 등의 읽기 scope를 먼저 설정하고, 그 scope가 있는 사용자 토큰을 발급합니다. ID token이나 AWS Access Key를 입력하지 마세요. 현재 파일 목록은 애플리케이션 공통 데이터이며 사용자별 접근 제어/시나리오 저장 기능은 없습니다.

저장소 루트에서:

```powershell
sam validate --lint --template-file infra/aws/template.yaml
sam build --template-file infra/aws/template.yaml
sam deploy --guided
```

`AllowedOrigin`: 재생 화면의 정확한 HTTPS origin(경로·마지막 슬래시 제외).
`JwtIssuer`: 인증 공급자의 issuer URL.
`JwtAudience`: 토큰 audience 또는 Cognito 앱 client ID.
`ReadScope`: 실제 발급 토큰에 포함되는 읽기 scope.

배포는 IAM 역할과 API를 생성합니다. 생성된 `ScenarioApiUrl`과 토큰을 재생 화면의 ‘AWS 시나리오 API 연결’에 입력합니다. 키·토큰·개인용 `samconfig.toml`은 커밋하지 않습니다. 중지할 때는 해당 스택을 `sam delete`로 제거할 수 있습니다.

## 배포 후 확인

1. 토큰 없이 GET 요청 → 401/403.
2. 잘못된 issuer/audience, 만료 토큰, 읽기 scope 없는 토큰 → 거부.
3. 유효한 access token → 목록과 시나리오 200, JSON Content-Type.
4. 없는 시나리오 → 404. POST/업로드/제어 경로는 제공하지 않음.
5. 설정된 웹 origin에서 Authorization 헤더를 포함한 OPTIONS/GET 허용.
6. 화면에서 클라우드 데이터 표시 확인 후 네트워크 실패 시 현재 장면 유지.

JWT 검증과 CORS는 API Gateway가 수행하므로 로컬 Lambda 단위 테스트 통과만으로 위 배포 검증을 대신할 수 없습니다.

## 나중에 확장할 위치

승인된 가상 시나리오 파일이 늘어나면 Lambda의 파일 읽기 부분을 사전에 검토한 S3 객체 읽기로 바꿀 수 있습니다. 이 경우 해당 버킷/접두사에 한정한 읽기 권한을 추가하고 기존 JSON 계약을 유지합니다. UI나 재생 계산은 바꾸지 않습니다. 현재는 S3 버킷을 생성하지 않으며 쓰기 API도 없습니다.

공식 참고:
- [SAM JWT authorizer](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-controlling-access-to-apis-oauth2-authorizer.html)
- [HTTP API Lambda payload v2](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html)
- [SAM HTTP API CORS](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-httpapi-httpapicorsconfiguration.html)
