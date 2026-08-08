# election2026 — 2026 미국 중간선거 모니터

**세 채널을 나란히 보여준다. 하나로 합치지 않는다.**

```
   ┌─ 베팅 시장 ──────────────────────────────────────────┐
   │  Polymarket. 어떤 선거를 보여줄지도 이쪽이 정한다 —   │
   │  거래액 순으로 줄 세운 시장의 목록이 곧 보드다.       │
   │  의석수·다수당 확률은 챔버 시장에서 그대로 읽는다.    │
   └──────────────────────────────────────────────────────┘
   ┌─ 여론조사 ───────────────────────────────────────────┐
   │  수기 스프레드시트 (2026년 무료 API가 없다).          │
   │  마진 → 확률 Φ(margin/σ).                            │
   └──────────────────────────────────────────────────────┘
   ┌─ 기초 지표 4개 모델 ─────────────────────────────────┐
   │  경제 / 정치자금 / 풀뿌리 / 관심도                    │
   │  각각 "어느 쪽으로, 얼마나 세게" — 확률이 아니다.     │
   └──────────────────────────────────────────────────────┘
```

## v2에서 무엇이 바뀌었나 (2026-08-08)

v2는 이 셋을 하나로 접었다: `p_alpha = p_consensus + λ·signal`.

**그 λ = 0.10은 수기값이었고 한 번도 적합된 적이 없다** (`CALIBRATION_VALIDATED
= False`). 적합에 쓸 과거 표본이 47개뿐이고 그중 상당수가 근사 재구성값이라
적합 자체가 의미를 갖지 못했다. 즉 보드 한복판의 대표 숫자를 아무도 방어할 수
없었다.

v3는 섞지 않는다. 섞지 않으면 λ를 정당화할 필요가 없다. 그래서
`divergence.py`, `p_consensus`, `p_alpha`, `delta`, `flagged`, `flag_history`,
`backtest`, 그리고 그것들을 떠받치던 `signal.combine`/`agreement`/
`coverage` 보정이 전부 삭제됐다. 남은 것은 각 채널이 실제로 말한 값이다.

부수 효과로 **네브래스카 문제가 저절로 풀렸다.** v2는 폴리마켓("민주당이
이기는가" 0.01)과 폴(오스본 vs 리케츠 0.795)을 평균 내 0.215를 만들었는데,
이는 어느 채널도 믿지 않는 숫자였고 그래서 그 레이스를 하드코딩으로
제외해야 했다. v3는 두 값을 그냥 나란히 놓고, "시장 확률의 26%가 민주·공화
어느 쪽도 아닌 후보에 걸려 있다"고 표시한다 — 하드코딩 없이 일반 규칙으로.

## 빠른 시작

```bash
python3 -m pip install -r election2026/requirements.txt

# 하루 한 번, 이 한 줄
python3 -m election2026 run --skip gdelt,reddit,youtube

# 대시보드
python3 -m election2026.serve    # http://localhost:8000/dashboard.html
```

`--skip gdelt,reddit,youtube`를 **꼭 붙일 것.** 셋 다 가중치 0이라 결과는
같은데, 안 붙이면 GDELT의 429 재시도로 1시간 40분 멈춘다.

`dashboard.html`은 파일을 직접 열면 동작하지 않는다 (fetch가 file://에서
막힌다). 반드시 `serve`를 거칠 것 — 페이지가 그 안내를 스스로 띄운다.

## CLI

```bash
python3 -m election2026 run                        # 라이브
python3 -m election2026 run --chamber senate       # 범위 제한
python3 -m election2026 run --races TX,MI          # 주 코드/race_id
python3 -m election2026 run --rank-by volume1wk    # 최근 1주일 거래액 순으로
python3 -m election2026 backfill [--weeks 8]       # 기초 지표 baseline 백필
python3 -m election2026 import-polls <xlsx...>     # NYT 시트 → polls
python3 -m election2026 make-templates             # 수동 입력 템플릿
python3 -m election2026 validate [path]            # 스키마 검증
python3 -m election2026 verify-log                 # 봉인 로그 검증
```

## 보드는 어떻게 정해지는가

`data/reference/races.json`은 **더 이상 레이스 목록이 아니다.** 현직·등급·과거
결과를 붙여주는 참조 테이블로 강등됐다. 보드에 오르려면 시장에서 거래되기만
하면 되고, 이 파일에 있다고 보드에 오르지도 않는다.

`board.py`가 폴리마켓 `midterms` 태그를 페이징으로 전부 긁는다 (2026-08-08
실측 **640개** — 하원 443, 상원 35, 주지사 35 + 챔버·잡다 시장). 제목이 정확히
`<주> Senate Election Winner` / `<주> Governor Election Winner` /
`<XX>-<NN> House Election Winner` 형태인 것만 레이스로 인정하고 나머지는
버린다.

### ⚠️ 하원에서 거래액은 경쟁도가 아니다

**이건 설계상의 한계이고, 사용자가 수치를 보고 내린 결정이다 (2026-08-08).**

폴리마켓은 하원 443개 지역구를 전부 상장한다. 누적 거래액은 "얼마나 오래
상장돼 있었나 + 얼마나 굴러다녔나"에 가깝지, 접전인지와는 무관하다. 그래서
누적 기준 상위권은 CA-28·FL-01·MS-01 같은 **안전 의석**이 차지한다. 실측:
기존에 손으로 고른 경합 18곳은 **전부 하원 중앙값 $23.7K 아래**였다.

| 우리 경합지 | 누적 거래액 |
|---|---|
| wa-03 | $26,981 |
| oh-09 | $25,682 |
| va-02 | $20,405 |
| … | … |
| pa-08 | $1,251 |
| ca-22 | 시장 없음 |

`config.BOARD["rank_by"]` 기본값은 `volume`(누적)이다. 폴리마켓 사이트가
보여주는 순서와 같게 하려는 사용자의 결정이다 (2026-08-08). 대신 대시보드가
`volume1wk`(최근 1주일)로 바꿔 볼 수 있는 토글을 제공하고, 그래서 `rank()`는
**두 기준 상위 N개의 합집합**을 담는다 — 한쪽으로 고른 집합을 다른 쪽으로
정렬하면 진짜 상위권이 빠진 채로 줄만 세우는 셈이기 때문이다.

일(日) 단위를 쓰지 않는 이유: 너무 짧아 의미가 없다. 알래스카 상원은 누적
$383K·유동성 $90K인데 24시간 거래가 $29였다(같은 주 1주일은 $6,459).

그래도 하원에서는 안전 의석이 상단에 올라올 수 있고, **그 행들은 여론조사·경제
칸이 비어 보인다** — 고장이 아니라 사실이다. 대시보드가 하원 표 위에 이
경고를 직접 띄운다.

경합지만 보고 싶으면 `--races`로 좁히거나 `config.BOARD["top_n"]`을 키울 것.

## 베팅 확률 읽기 — 세 가지 교정

`track_a/adapters.py`의 옛 방식(민주당 다리 가격을 그대로 읽기)은 세 군데서
틀렸다. `board._two_party()`가 전부 고친다.

1. **양쪽 다리를 읽고 정규화한다.** 폴리마켓은 D·R 가격을 따로 매기고 합이
   1이 아니다 — 텍사스 상원 2026-08-08: D 0.495 / R 0.515 (합 1.010). D만
   읽으면 그 스프레드가 편향으로 들어온다. → `D / (D + R)`
2. **정당별로 합산한다.** 알래스카 주지사는 정글 프라이머리라 공화 후보
   12명·민주 후보 4명이 한 시장에 올라간다. 선두 공화 후보의 0.335는 공화당
   확률이 아니다.
3. **민주·공화 어느 쪽도 아닌 확률을 보고한다** (`unmapped_mass`).
   10%(`board.UNMAPPED_WARN`)를 넘으면 `trustworthy = False`가 되고 대시보드가
   숫자 대신 경고를 띄운다. 현재 걸리는 곳: **네브래스카 26%, 몬태나 17%**.
   스키마가 이 일관성을 강제하므로 (`test_schema_v3.py`) 무소속이 큰 레이스를
   깨끗한 두 정당 확률처럼 발행할 수 없다.

한쪽 정당 다리가 **아예 없으면 확률은 None이다.** `0.0`이 아니다 — 다리가
없는 것과 "확률이 0"은 다른 사실이고, 후자로 표시하면 없는 확신을 만든다.

정당 라벨이 없는 후보(알래스카)는 `data/reference/candidate_parties.json`에서
해결한다. 빠뜨린 후보는 `unmapped_mass`로 집계되므로 **조용히 틀리지 않고
표시가 멈춘다.**

## 의석수와 다수당 확률 — 계산하지 않고 읽는다

`chambers.py`는 집계하지 않는다. 애초에 불가능하다: 보드에는 수십 개 레이스뿐인데
하원은 435석이고, 안 보여주는 레이스가 정확히 결과를 가정해야 하는 레이스다.

대신 시장을 직접 읽는다.

| 시장 | 거래액 | 쓰임 |
|---|---|---|
| Balance of Power: 2026 Midterms | $9.5M | 상·하원 조합 결과 (대시보드 최상단) |
| Which party will win the House | $9.1M | 하원 다수당 확률 |
| Which party will win the Senate | $3.8M | 상원 다수당 확률 |
| Republican Senate seats after… | $2.8M | 상원 의석 분포 |
| Republican House seats after… | $282K | 하원 의석 분포 |
| How many Republican Governors… | $686K | 주지사 분포 |

**다수당 확률은 의석 분포가 아니라 "어느 당이 이기는가" 시장에서 읽는다.**
장악은 의석 수의 깔끔한 함수가 아니기 때문이다 — 50-50 상원은 부통령이
가른다. 거래자들은 그 규칙을 반영해 가격을 매기지만, 중앙값 합산은 못 한다.

교차검증이 하나 나온다: 상원 장악 시장이 민주 45.5%인데, 의석 분포에서
P(공화 ≤ 49) = 46.2%로 거의 일치한다.

**의석 기댓값은 어림값이다.** 양 끝 구간(`Below 190`이 21.6%, `≤47`이 22.6%)은
상·하한이 열려 있어 중앙값을 가정해야 한다. 그래서 대시보드는 **막대 분포를
주인공**으로 두고 기댓값은 각주로 내린다. 스키마의
`expectation_is_approximate`가 이 사실을 문서에 실어 나른다.

## 기초 지표 4개 모델

15개 변수를 사람이 읽을 수 있는 질문 4개로 묶었다 (`models.py`).

| 모델 | 질문 | 변수 |
|---|---|---|
| 경제 | 주 경제가 좋아지나, 나빠지나? | 실업수당 청구·경기동행지수·실업률 (FRED) |
| 정치자금 | 어느 쪽이 지역 기부자를 더 움직이나? | FEC 5종 (주내 비중·소액기부 건수·고유 기부자·재기부율·소진율) |
| 풀뿌리 | 실제 투표 행동은 어느 쪽인가? | 예비선거 투표율 격차·정당 등록 순변동 |
| 관심도 | 이 선거가 평소보다 주목받나? | 위키 조회·편집 (+ 가중치 0인 gdelt/reddit/youtube) |

**출력은 확률이 아니라 방향과 세기다.** z-score는 "이 지표가 자기 평소 수준
대비 1.4 표준편차"라고 말할 뿐이고, 이를 "민주당 57% 승리"로 바꾸려면 지표와
결과를 잇는 적합된 대응 관계가 필요한데 그게 없다. 상세보기의 `±X%p`는 같은
z를 익숙한 단위로 환산한 참고치이며 (`config.PP_PER_SIGMA = 5.0`), 그 값은
옛 λ=0.10·z_norm=2.0에서 그대로 이어받아 추적 가능하게 두었다.

**관심도는 방향을 갖지 않는다.** 조회수는 지지와 반감을 구분하지 못한다.
그래서 정당이 아니라 수준(높음/보통/낮음)만 보고하고, `shift_pp`도 없다.
스키마가 이걸 강제한다.

모델 안에서 가중치는 **가용한 변수 위에서만 재정규화**된다. 5개 중 1개만
살아 있으면 그 1개의 값을 보고하지, 5분의 1로 눌린 값을 보고하지 않는다.
대신 `n_available/n_total`을 같이 실어서 읽는 사람이 스스로 할인하게 한다 —
v2의 `sqrt(n/N)` 보정은 하나로 합친 신호를 레이스 간 비교 가능하게 만들려던
장치였고, 합치지 않는 지금은 보정할 대상이 없다.

`structural`(제도적으로 불가 — 주지사 선거는 FEC 관할 밖) / `pending`(아직
시점이 안 됨) / `missing`(이번에 실패)는 **서로 다른 사실**이고 스키마 수준에서
구분된다.

## 데이터 계약 (schema.py, v3.1.0)

```
{meta, balance_of_power[], chambers{senate,house,governor}, races[], movers[]}
```

레이스 하나:
```
race_id, chamber, state, district, label, title, rank,
betting {prob_dem, volume, volume1wk, liquidity, slug,
         unmapped_mass, trustworthy, change_1d, change_7d},
polls   {prob_dem, n_polls, latest_date, margin_dem} | null,
models  [{key, label, question, lean|level, z, strength, shift_pp,
          n_available, n_total, variables[], unavailable, reason}],
candidates {D, R},
reference {incumbent, incumbent_party, seat_party, rating, history[]} | null
```

모든 run은 저장 **전에** 검증하고 위반 시 전체 목록과 함께 실패한다. 형상
변경 = `SCHEMA_VERSION` 범프.

스키마가 강제하는 교차 불변식:
- `betting.trustworthy`는 `unmapped_mass`에서 따라 나와야 한다 (자유롭게 못 씀)
- 관심도 모델은 정당 lean도 `shift_pp`도 가질 수 없다
- 방향성 모델은 attention level을 가질 수 없다
- 가용 변수가 0인 모델은 반드시 사유를 갖는다
- `rank`는 챔버별로 1..n 연속

## 변화 트래킹

`data/history/<날짜>.json`에 매 full run의 스냅샷이 쌓이고, 다음 run이
베팅 확률의 1일·7일 변화를 계산해 `betting.change_1d/7d`와 `movers[]`에
싣는다. **v2 스냅샷은 건너뛴다** — v2의 대표 숫자는 베팅·폴 혼합값이라
베팅 가격으로 취급하면 개편 당일에 가짜 변동이 만들어진다.

즉 **v3 두 번째 run부터 변화량이 나온다.** 첫 run에 `movers`가 비어 있는 건
정상이다.

## 수동 스프레드시트 (polls · ratings · primary_turnout · party_registration)

무료 API가 없는 소스는 1급 수동 경로다. `make-templates`로 `data/manual/`에
템플릿을 만들고, `_template`을 뗀 이름으로 저장하면 `run`이 자동 검증한다
(미지의 race_id·잘못된 날짜·범위 밖 마진은 행 번호를 명시한 에러로 즉시 실패).

NYT 여론조사 시트는 `import-polls`로 변환한다. 출력이 합집합이므로 **항상 두
워크북을 함께** 넘길 것:

```bash
python3 -m election2026 import-polls 2026_senate_poll.xlsx 2026_House_poll.xlsx
```

폴 집계는 반감기 14일 지수 감쇠 + √표본크기 + 매치업 신뢰도 가중
(confirmed 1.0 / generic_ballot 0.60 / hypothetical 0.35 / withdrawn 0.15).
한 조사의 여러 매치업은 1행으로 접는다 — 안 그러면 가중치가 뻥튀기된다.

## 소스별 제약 (2026-08-08 실측)

| 소스 | 인증 | 제약 |
|---|---|---|
| polymarket | 불필요 | 사실상 무제한. `midterms` 태그 640개를 1회 페이징으로 |
| fec | FEC_API_KEY (무료) | 시간당 1,000콜. 연방 선거만 — 주지사는 **구조적 불가**. 신고 지연 ~45일(이날 실측), frontier로 고정 |
| fred | 불필요 | 주 경제 3변수 |
| wiki | 불필요 | 2015년까지 백필 가능 — 유일하게 과거 재측정이 되는 소스 |
| gdelt | 불필요 | **가중치 0.** DOC API가 상시 429. 벌크 파일로 재구축해야 함 |
| reddit / youtube | 자격증명 필요 | **가중치 0.** 자격증명 미확보 |

## backfill — baseline을 기다리지 않는다

기초 지표는 자기 baseline 대비 z-score로만 들어오고, 관측치가
`baseline_min_obs`(4) 미만이면 z는 None이다. 그냥 주 1회 돌리면 4주 동안
침묵한다. `backfill`이 과거를 답할 수 있는 소스에 대해 최근 8주치를 한 번에
끌어와, 라이브 run과 **같은** 채널·기간 라벨로 같은 rolling baseline에 적는다.

- **가능**: `fec_*` 5종, `wiki_*` 2종, `gdelt`
- **불가**: `youtube`·`reddit` (현재 카운터만 조회 가능 — 8주 전 열의는
  복원이 아니라 날조), `primary_turnout_ratio`·`party_reg_net_change`
  (baseline이 주간이 아니라 과거 선거 사이클이며 수동 payload에 내장)

`--chamber`나 `--races`를 붙인 run은 **baseline을 쌓지 않는다** (부분 패널로
구조적 잔차를 적합하면 스케일이 어긋난다). 조회·디버깅용으로만.

## 레포 구조

```
dashboard.html               # 프런트. serve 경유로 forecast.json fetch
election2026/
  board.py                   # 시장 = 보드. 거래액 순 정렬, 두 정당 정규화
  chambers.py                # 의석수·다수당·Balance of Power 직접 판독
  models.py                  # 15개 변수 → 4개 모델 (방향 + 세기)
  schema.py                  # v3 계약 + 검증 (교차 불변식 포함)
  config.py                  # BOARD / TRACK_A / TRACK_B / PP_PER_SIGMA
  pipeline.py                # 수집 → 문서 → 검증 → 저장 (합치지 않음)
  track_a/                   # polymarket, kalshi, polls (margin→prob)
  track_b/                   # fec×5, econ×3, 예비선거, 정당등록, wiki×2, …
  baseline.py / signal.py    # rolling baseline·z-score / VariableReading
  manual.py / import_polls.py / backfill.py / prediction_log.py / serve.py
  data/reference/races.json          # 참조 데이터 (보드 목록 아님)
  data/reference/candidate_parties.json  # 정당 라벨 없는 후보 → 정당
  data/history/ | baselines/ | manual/ | raw/
```
