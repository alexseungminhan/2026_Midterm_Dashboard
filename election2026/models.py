"""models.py — the Track B variables, split into four models a person can read.

The old pipeline collapsed fifteen variables into ONE number and then folded
that into the consensus probability (`p_alpha = p_consensus + λ·signal`). Two
problems with that, and the second is the serious one:

  1. Nobody could see what the number was made of.
  2. λ = 0.10 was hand-set and never fitted (config.CALIBRATION_VALIDATED is
     False). Blending an unvalidated coefficient into the headline probability
     put an indefensible number at the centre of the board.

Unbundling fixes both at once. Four models, each answering a question in plain
language, each shown next to the betting and polling numbers rather than mixed
into them. Nothing here touches the market price, so nothing here needs λ.

    경제      주 경제가 좋아지는가, 나빠지는가 — 집권당에 유리한가
    정치자금   어느 쪽 후보가 지역 소액 기부자를 더 움직이고 있는가
    풀뿌리     실제 투표 행동(예비선거 투표율·정당 등록)이 어느 쪽으로 기우는가
    관심도     이 선거구 자체가 평소보다 주목받고 있는가  ← 방향 없음

WHAT THESE OUTPUT. A lean (D/R/neutral) and a strength, not a win probability.
A z-score says "this indicator is 1.4 standard deviations above its own
baseline"; converting that into "the Democrat wins 57%" needs a fitted mapping
from indicator to outcome, and the only historical sample available is n=47
with mostly reconstructed columns. `shift_pp` exists because the detail view
asks for a number, and it carries PP_PER_SIGMA's health warning with it.

관심도 is deliberately directionless. A pageview cannot separate support from
outrage, so the attention model reports a LEVEL ("평소보다 주목도 높음") and
never a party. That constraint predates this redesign and survives it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config

# One model = one question. `vars` are config.TRACK_B["weights"] keys; a
# variable weighted 0.0 there stays listed but contributes nothing, so zeroing
# a broken source (GDELT, Reddit, YouTube) does not require editing this map.
MODELS = {
    "economy": {
        "label": "경제 모델",
        "question": "주 경제가 좋아지고 있나, 나빠지고 있나?",
        "detail": "주 실업수당 청구·실업률·경기동행지수를 그 주의 과거 수치와 "
                  "비교해 최근 몇 달의 방향을 낸다. 좋아지면 책임지는 쪽에 "
                  "유리하게 읽는데, 그 대상이 직위마다 다르다 — 상·하원은 "
                  "대통령 정당(현재 공화당), 주지사는 그 자리를 쥔 정당이다. "
                  "그래서 같은 주라도 상원과 주지사의 부호가 반대로 나온다.",
        "vars": ["econ_claims", "econ_coincident", "econ_unemployment"],
        "directional": True,
        # The three series are a percentage, an index and a rate — averaging
        # them would let whichever swings widest decide. Count which way each
        # one points instead; the result is -1..+1 like every other model.
        "aggregate": "tally",
    },
    "money": {
        "label": "정치자금 모델",
        "question": "어느 쪽 후보가 기부를 더 많이 받는가?",
        "detail": "FEC 신고 내역에서 소액기부 건수·고유 기부자 수·주내 기부 "
                  "비중 세 가지를 두 후보끼리 직접 비교한다 — "
                  "(민주 − 공화) / (민주 + 공화). 총액이 아니라 '몇 명을 "
                  "움직였나'를 보고, 한쪽이 0이면 비교하지 않는다.",
        "vars": ["fec_in_state_share", "fec_small_dollar_count",
                 "fec_unique_donors", "fec_repeat_donor_rate", "fec_burn_rate"],
        "directional": True,
        # Count which way each variable points rather than averaging the
        # normalized contrasts (2026-08-09, user's call). A share and a count
        # are different quantities; the tally does not pretend otherwise.
        "aggregate": "tally",
    },
    "grassroots": {
        "label": "예비선거 모델",
        "question": "예비선거 투표율 비교/분석",
        "detail": "각 당의 2026 예비선거 투표수가 그 당의 2022 투표수 대비 "
                  "몇 %인지 구한 뒤, 두 값을 직접 비교한다. 설문이 아니라 "
                  "실제로 투표소에 간 사람 수다.",
        "vars": ["primary_turnout_ratio", "party_reg_net_change"],
        "directional": True,
    },
    "attention": {
        "label": "관심도 모델",
        "question": "어느 쪽 후보가 더 주목받고 있나?",
        "detail": "두 후보의 위키백과 조회수·편집 횟수를 직접 견준다 — "
                  "(민주 − 공화) / (민주 + 공화). 후보 이름은 베팅 시장이 "
                  "싣고 있는 실제 출마자를 쓴다. 주의: 조회수는 유명세이지 "
                  "지지가 아니다. 스캔들도 조회수를 올린다.",
        "vars": ["wiki_pageviews_share", "wiki_edit_count"],
        "directional": True,
        "aggregate": "tally",
    },
}

# |weighted z| -> strength bucket. Hand-set reading aids, not thresholds with
# statistical meaning; they exist so the dashboard can say "약간" vs "뚜렷하게".
TOO_SMALL = 0.1        # see the tally branch in build()
STRENGTH_EDGES = (0.25, 0.75, 1.50)
STRENGTH_WORDS = ("중립", "약함", "보통", "강함")


def strength_of(z: float) -> int:
    for i, edge in enumerate(STRENGTH_EDGES):
        if abs(z) < edge:
            return i
    return len(STRENGTH_EDGES)


@dataclass
class VariableDetail:
    """One variable inside a model, for the detail view."""
    variable: str
    label: str
    z: Optional[float]
    weight: float
    availability: str               # available | missing | structural | pending
    reason: Optional[str] = None
    dem_value: Optional[float] = None
    rep_value: Optional[float] = None
    # The reading in the source's own units, when it differs from z (economy).
    raw_value: Optional[float] = None


@dataclass
class ModelReading:
    key: str
    label: str
    question: str
    detail: str
    directional: bool
    # Directional models: "D" | "R" | "N"(중립).  Attention model: always None.
    lean: Optional[str] = None
    # Attention model only: "high" | "normal" | "low".
    level: Optional[str] = None
    z: Optional[float] = None       # weighted mean over available variables
    strength: int = 0               # index into STRENGTH_WORDS
    shift_pp: Optional[float] = None
    n_available: int = 0
    n_total: int = 0
    variables: list = field(default_factory=list)
    # Set when nothing was usable, so the UI distinguishes "제도적으로 없음"
    # from "이번에 못 가져옴" instead of drawing an empty bar for both.
    unavailable: Optional[str] = None
    reason: Optional[str] = None

    @property
    def strength_word(self) -> str:
        return STRENGTH_WORDS[self.strength]


def _summarize_unavailability(details: list) -> tuple:
    """(availability, reason) for a model where no variable produced a z.

    "structural" wins over "missing" when every usable variable is
    structurally absent — the FEC has no jurisdiction over governor races, and
    reporting that as a fetch failure would send the reader looking for a bug.
    """
    live = [d for d in details if d.weight > 0]
    if not live:
        return "structural", "이 모델에 배정된 변수의 가중치가 모두 0이다"
    kinds = {d.availability for d in live}
    if kinds == {"structural"}:
        return "structural", live[0].reason
    if "pending" in kinds:
        pending = next(d for d in live if d.availability == "pending")
        return "pending", pending.reason
    missing = next((d for d in live if d.availability == "missing"), None)
    return "missing", missing.reason if missing else "데이터 없음"


def build(readings: list, weights: Optional[dict] = None) -> list:
    """[ModelReading] for one race, from its Track B VariableReadings.

    Weights are renormalized over the AVAILABLE variables within each model,
    so a model with one of five inputs still reports that input rather than a
    value dragged four-fifths of the way to zero. `n_available/n_total` rides
    along so the reader can discount it themselves — which is the honest
    alternative to the old sqrt(n/N) correction, whose only purpose was making
    a single blended number comparable across races.
    """
    weights = weights or config.TRACK_B["weights"]
    names = config.TRACK_B["display_names"]
    by_var = {r.variable: r for r in readings}

    out = []
    for key, spec in MODELS.items():
        details = []
        for var in spec["vars"]:
            r = by_var.get(var)
            details.append(VariableDetail(
                variable=var,
                label=names.get(var, var),
                z=(round(r.z, 3) if (r is not None and r.z is not None) else None),
                weight=weights.get(var, 0.0),
                availability=(r.availability if r is not None else "missing"),
                reason=(r.reason if r is not None else "변수가 이번 run에 없었다"),
                dem_value=(r.dem_value if r is not None else None),
                rep_value=(r.rep_value if r is not None else None),
                raw_value=(round(r.raw_value, 3)
                           if (r is not None and r.raw_value is not None)
                           else None),
            ))

        model = ModelReading(
            key=key, label=spec["label"], question=spec["question"],
            detail=spec["detail"], directional=spec["directional"],
            n_total=sum(1 for d in details if d.weight > 0),
            variables=details,
        )

        live = [d for d in details if d.z is not None and d.weight > 0]
        model.n_available = len(live)
        if not live:
            model.unavailable, model.reason = _summarize_unavailability(details)
            out.append(model)
            continue

        if spec.get("aggregate") == "tally":
            # A tally ignores size, so a 0.05% year-over-year move would count
            # exactly like a 5% one. TOO_SMALL is the floor below which a
            # series is called flat: 0.1 in the series' own unit, i.e. 0.1%
            # for the percent-change series and 0.1pp for unemployment.
            # Judgement, not a fitted threshold — but a threshold of zero is
            # also a judgement, and a worse one.
            votes = [d for d in live if abs(d.z) >= TOO_SMALL]
            up = sum(1 for d in votes if d.z > 0)
            down = sum(1 for d in votes if d.z < 0)
            # Denominator is every variable the model WANTS, so one series
            # out of three agreeing reads as weak rather than unanimous.
            z = (up - down) / len(live)
        else:
            total_w = sum(d.weight for d in live)
            z = sum(d.weight * d.z for d in live) / total_w
        model.z = round(z, 3)
        model.strength = strength_of(z)
        model.shift_pp = round(z * config.PP_PER_SIGMA, 1)

        if spec["directional"]:
            model.lean = "N" if model.strength == 0 else ("D" if z > 0 else "R")
        else:
            # Attention has no party. Its sign is "busier / quieter than this
            # race's own normal", which is a level, not a lean.
            model.level = ("normal" if model.strength == 0
                           else ("high" if z > 0 else "low"))
            model.shift_pp = None      # a lean-free model cannot shift anything

        out.append(model)
    return out
