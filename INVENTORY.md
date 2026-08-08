# Step 0 — 기존 코드베이스 인벤토리 (2026-07-26)

> **⚠️ 이 문서는 과거 기록이다.** 2026-07-26 리팩토링 직전의 스냅샷이고,
> 아래 "신규 아키텍처"는 **그때의 계획**이다. 그 계획은 v2로 구현됐다가
> 2026-08-08 v3에서 상당 부분 폐기됐다. **현재 구조는 `election2026/README.md`를
> 볼 것.** 이 문서는 "왜 지금 코드가 이렇게 생겼나"의 출발점으로만 유효하다.
> 문서 끝의 §"계획 → 실제" 표가 그 차이를 정리한다.

리팩토링 전 기준. 이 문서 보고 후에만 삭제/재작성 진행.

## 기존 모듈

| 모듈 | 역할 | 판정 |
|---|---|---|
| `election2026/collect.py` | 소스 어댑터(Polymarket, Kalshi, 수동 폴, 수동 레이팅, YouTube, GDELT) + 디스크 캐시 + 쿼터 원장 + rate limiter | **부분 재사용.** 어댑터 인터페이스/캐싱/never-raise 패턴/Polymarket·Kalshi·GDELT 파서/쿼터 원장은 신규 구조로 이식. "ensemble_channel" 개념(배팅+폴+레이팅 혼합)은 폐기 |
| `election2026/forecast.py` | **가중 앙상블**(betting 0.45 / model 0.35 / polls 0.20) → 단일 혼합 확률 + Monte Carlo 의회 시뮬 + 모멘텀 + 코멘트 | **폐기.** 앙상블 = 공개 컨센서스 재생산이라는 구방법론의 핵심. Monte Carlo도 신규 계약에 없음. 4분면 한국어 코멘트 생성기 아이디어만 comment.py로 승계 |
| `election2026/normalize.py` | 폴 마진→확률(norm.cdf), 레이팅→확률, 마켓 확률 클램프 | **재사용.** 순수 함수라 Track A 컨센서스 계산에 그대로 유효 |
| `election2026/config.py` | 앙상블 가중치·티어 임계값·레이스 정의·수동 폴/레이팅 테이블·mock 데이터 | **부분 재사용.** 레이스 메타데이터, RATING_TO_PROB, Polymarket slug, mock 골격은 유지. ENSEMBLE_WEIGHTS/TIER/MOCK_SOCIAL 등 구조는 폐기. Track A/B 설정 섹션 분리로 재작성 |
| `election2026/serve.py` | stdlib 정적 서버 + /forecast.json CORS | **재사용.** 대시보드 경로만 divergence-monitor.html로 변경 |
| `election2026/tests/` | ensemble·normalize 단위 테스트 | normalize 테스트 재사용, ensemble 테스트 폐기(대상 소멸) |
| `2026 US Election Forecast.html` | 구 프런트엔드 | **폐기(보존).** divergence-monitor.html로 대체 |
| `election2026/data/raw/*` | 소스별 캐시(JSON) | 캐시 산출물 — 그대로 두되 신규 트리(track_a/, track_b/)로 재편 |

## 신규 프런트엔드 (divergence-monitor.html)

- Claude 아티팩트 번들 포맷(14MB 단일 파일): line 372 = gzip+base64 매니페스트(React/Babel/폰트), line 384 = JSON 인코딩된 실제 페이지(DCLogic React 컴포넌트, 7,452줄 상당).
- 데이터는 `Component.static RACES` 하드코딩 mock (상원 12 / 하원 24 / 주지사 9 레이스). fetch 없음 → Step 8에서 forecast.json fetch + mock 폴백으로 배선.
- UI가 읽는 필드: `race_id, chamber('senate'|'house'|'gov'), state, label, district, incumbent, incumbent_party, seat_party, rating(code), p_consensus, p_alpha, delta(%p), flagged, bands('T|LD|…'), signals[{name,direction,sigma}], history[{year,type,party,margin,who}], comment` + `CH{baseD,baseR,threshold,total}` + `DATES[]`.
- 계약과의 차이 → 스키마 확장으로 해소(UI 수정 금지 원칙): `label`, `seat_party`, `history[].who`, `history[].type`에 'governor'/'house', chambers에 `total_seats` 추가. rating 문자열("Lean D")↔코드('lean_d'), chamber "governor"↔'gov', delta 소수↔%p, bands/DATES↔flag_history 변환은 fetch 글루 코드에서 수행.

## 신규 아키텍처 (재작성 대상)

```
election2026/
  schema.py            # Step 1: 동결된 데이터 계약 + 검증 (schema_version 2.0.0)
  cache.py             # 캐시/재시도/rate-limit 공용 플러밍 (collect.py에서 이식)
  adapter.py           # is_available()/fetch 공용 베이스
  manual.py            # Step 3: xlsx/csv 수동 입력 + make-templates
  track_a/             # 컨센서스 전용 — polymarket, kalshi, polls(수동), ratings, consensus.py
  track_b/             # 기저 신호 전용 — fec, gdelt, trends, youtube, twitter(stub), adspend(수동), quota.py
  baseline.py          # rolling baseline + z-score (±3 clip, 최소 관측 가드)
  signal.py            # 가중 결합 + 일치도 감쇠
  divergence.py        # p_alpha = clip(p_c + λ·signal), flag, flag-rate 경고
  comment.py           # 규칙 기반 한국어 + env-gate LLM
  backtest.py          # λ 스윕, Brier, flag P/R, LOCO CV
  pipeline.py + cli    # run/--dry-run/--chamber/--races, make-templates, backtest, validate
  mockdata.py          # 2026-07 실제 랜드스케이프 mock (프런트 mock에서 추출)
```

Track A와 Track B는 패키지·config 섹션·raw 캐시 디렉터리를 모두 분리해 입력 공유를 구조적으로 차단.

---

## 계획 → 실제 (2026-08-08 v3 시점에서 되짚음)

위 계획의 절반은 살아남았고 절반은 지워졌다. 갈린 기준은 하나다:
**"이 코드가 만드는 숫자를 방어할 수 있는가."**

블렌딩 계층(`p_alpha = p_consensus + λ·signal`)은 방어할 수 없었다. λ = 0.10은
수기값이었고 한 번도 적합되지 않았다 — 적합에 쓸 과거 표본이 47개뿐이었고
그중 상당수가 근사 재구성값이었다. 보드 한복판의 대표 숫자를 아무도 방어할 수
없다는 뜻이라, v3는 섞기를 그만두고 **세 채널을 나란히 놓는 쪽**을 택했다.

| 계획 모듈 | 실제 | 사유 |
|---|---|---|
| `schema.py` | **생존** (v3.0.0) | 계약 + 교차검증 규칙 |
| `cache.py` `adapter.py` `manual.py` | **생존** | 플러밍은 그대로 유효 |
| `track_a/` `track_b/` | **생존** | 트랙 분리 원칙 유지 |
| `baseline.py` | **생존** | z-score는 여전히 기초 지표의 단위 |
| `pipeline.py` + cli | **생존** | `backtest` 서브커맨드만 제거 |
| `signal.py` | **축소** | `combine`/`agreement`/`coverage` 감쇠 삭제. 합치지 않으니 감쇠할 대상이 없다 |
| `divergence.py` | **삭제** | λ를 정당화할 수 없었다 |
| `track_a/consensus.py` | **삭제** | 시장·폴을 하나로 접지 않는다 |
| `backtest.py` | **삭제** | 표본 47건으로는 적합이 의미를 갖지 못한다 |
| `comment.py` | **삭제** | 방어 못 하는 숫자에 붙는 해설이었다 |
| `mockdata.py` + `data/mock/` | **삭제** | 실데이터로 대체 (2026-08-08 파일도 삭제) |
| `normalize.py` | 흡수 | 마진→확률 Φ는 `track_a/polls.py`에 |
| — | **신규** `board.py` | 보드를 시장이 정한다 (거래액 순). `races.json`은 참조 테이블로 강등 |
| — | **신규** `chambers.py` | 의석·다수당 확률을 **집계하지 않고 시장에서 읽는다** |
| — | **신규** `models.py` | 15개 변수 → 사람이 읽는 질문 4개 (경제/정치자금/풀뿌리/관심도) |

프런트엔드도 교체됐다. 위에 적힌 14MB 아티팩트 번들(`divergence-monitor.html`)은
`dashboard.html`로 대체됐고, 2026-08-08 구 파일 3개(`divergence-monitor.html`,
`.bak`, `2026 US Election Forecast.html` — 합계 약 33MB)는 삭제됐다.

`data/historical/`은 **남겨 뒀다.** 백테스트는 지워졌지만 그 안의
`MANIFEST.md`가 "왜 확률로 환산하지 않는가"의 근거이고,
`config.CALIBRATION_NOTE`가 화면에서 이 파일을 직접 인용한다.
