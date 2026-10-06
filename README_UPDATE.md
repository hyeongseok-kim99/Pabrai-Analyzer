# Pabrai-Analyzer v2 업데이트 가이드

## 이번 버전의 핵심

1. 기존 KOSPI 전체 목록을 앱의 활성 리스트에서 제외합니다.
2. KOSPI는 **시가총액 기준 상위 200개**만 활성 Universe로 사용합니다.
3. S&P 500은 현재 구성종목 전체를 활성 Universe로 사용합니다.
   - S&P DJI는 2026-08-31 기준 503 constituents를 표시합니다.
4. GitHub Actions가 703개 Universe를 순차적으로 자동 평가합니다.
5. 결과는 `Pabrai Score (자동 1차)` + `Coverage`로 표시됩니다.
6. Buffett-style / Munger-style 평가를 추가할 수 있는 공통 Framework 구조를 생성합니다.

## 중요한 점

자동 점수는 213개 문항을 근거 없이 전부 채우지 않습니다.

재무/시장 데이터로 정량화 가능한 요소를 자동 평가하며,
Circle of Competence, Personal Biases, 경영진 신뢰도,
Moat의 질적 지속가능성 등은 별도의 정성 분석 영역으로 남깁니다.

따라서 `Score`와 함께 반드시 `Coverage`를 확인해야 합니다.

---

# 설치 순서

## 1. Supabase SQL 실행

Supabase → SQL Editor에서:

`supabase_migration_v2.sql`

전체 내용을 붙여넣고 Run 합니다.

정상 실행 후 framework_count와 dimension_count가 표시됩니다.

---

## 2. GitHub Actions Secrets 등록

GitHub `Pabrai-Analyzer` repository:

Settings
→ Secrets and variables
→ Actions
→ New repository secret

다음 두 개를 등록합니다.

### SUPABASE_URL

Streamlit Secrets에 사용한 것과 동일한 Pabrai-Analyzer Supabase Project URL.

### SUPABASE_SECRET_KEY

Supabase `Secret keys`의 서버용 secret key.

Secret 값은 코드에 쓰지 않습니다.

---

## 3. ZIP 내용 GitHub에 업로드

ZIP을 PC에서 압축 해제합니다.

GitHub:
Add file → Upload files

압축을 푼 파일/폴더를 업로드합니다.

기존 `app.py`, `requirements.txt`는 덮어씁니다.

`.github/workflows/`
`scripts/`
폴더도 함께 올라가야 합니다.

---

## 4. Universe 생성

GitHub → Actions에서

`Sync KOSPI top 200 and S&P 500`

→ Run workflow

를 한 번 실행합니다.

정상 완료되면 앱 Dashboard에서:

- KOSPI 상위: 200
- S&P 500: 약 503
- 활성 Universe: 약 703

으로 표시됩니다.

기존 742개 나머지 KOSPI 종목은 `companies` 테이블에서 물리 삭제하지 않습니다.
대신 `investment_universe.is_active=false`로 처리되어 앱 리스트에서 사라집니다.

이 방식은 과거 분석 데이터가 실수로 삭제되는 것을 방지합니다.

---

## 5. 703개 자동 Pabrai Score 시작

Actions:

`Auto score investment universe`

→ Run workflow

기본 batch_size = 30입니다.

이 workflow는 매시간 자동 실행되고,
아직 점수가 없는 기업부터 순서대로 처리합니다.

703개 / 30개 ≈ 24회 실행이므로
외부 데이터 제공처가 정상 응답한다면 대략 하루 정도에 초기 1차 점수가 채워지는 구조입니다.

더 빠르게 시도하려면 수동 실행 시 batch_size를 50~100으로 높일 수 있지만,
Yahoo Finance의 rate limit 때문에 기본 30을 권장합니다.

점수가 이미 있는 기업은 30일이 지나기 전에는 다시 계산하지 않습니다.

---

# 자동 점수의 해석

`PABRAI_AUTO`는 공식 Mohnish Pabrai 점수체계가 아닙니다.

기존 213문항의 우선순위:

Leverage 24%
Moat 22%
Management & Ownership 20%
Business Economics 8%
Accounting 5%
Valuation 7%
External Risks 3%
Failure Points 5%
Circle of Competence 4%
Personal Biases 2%

를 바탕으로 정량화 가능한 재무/시장 Factor를 매핑합니다.

Circle of Competence와 Personal Biases는 자동 채점하지 않습니다.

Moat와 Management 역시 공개 숫자로 판단할 수 있는 일부 proxy만 사용합니다.

---

# 향후 확장

DB에는 다음 Framework가 미리 만들어집니다.

- PABRAI_AUTO
- BUFFETT_STYLE
- MUNGER_STYLE

Buffett-style과 Munger-style은 현재 구조만 생성하며,
향후 각 Dimension에:

- 재무 정량 Factor
- 공시/IR 근거
- 정성 체크리스트
- AI 보조 분석
- 사용자 최종 판단

을 연결할 수 있습니다.
