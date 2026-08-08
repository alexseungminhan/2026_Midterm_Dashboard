"""config.py — hand-editable configuration.

STRUCTURAL RULE: Track A (betting + polls) and Track B (base indicators) have
fully separate config sections and never read each other's entries.

Nothing here combines channels any more. The λ / flag-threshold block and the
time-varying consensus weights left on 2026-08-08 with the blending engine;
what remains configures WHICH races are shown (BOARD), how each model mixes
its own variables (TRACK_B["weights"]), and how the detail view glosses a
z-score in points (PP_PER_SIGMA).
"""

from __future__ import annotations

import json
import os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
REFERENCE_DIR = os.path.join(DATA_DIR, "reference")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")
HISTORICAL_DIR = os.path.join(DATA_DIR, "historical")

ELECTION_DATE = date(2026, 11, 3)

# Party holding the White House during this cycle. Used ONLY by the economic
# variables, to decide who a state's economic trajectory is credited to in a
# federal race: the midterm penalty is a referendum on the national
# administration, not on a first-term House member. Governor races credit
# their own seat_party instead (see FredBase).
PRESIDENT_PARTY = "R"

# Track-neutral reference data: both tracks name states, and neither owns the
# mapping. Track B renders API queries with it; Track A resolves the state
# column of the gubernatorial poll workbook back to a race_id.
# All 50 states + DC. The board is now built from whatever the betting market
# trades (board.py), not from a curated race list, so this map has to resolve
# every state Polymarket can name — not just the ones we hand-picked.
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Korean display names, for the dashboard. Falls back to the English name.
STATE_NAMES_KO = {
    "AL": "앨라배마", "AK": "알래스카", "AZ": "애리조나", "AR": "아칸소",
    "CA": "캘리포니아", "CO": "콜로라도", "CT": "코네티컷", "DE": "델라웨어",
    "DC": "워싱턴DC", "FL": "플로리다", "GA": "조지아", "HI": "하와이",
    "ID": "아이다호", "IL": "일리노이", "IN": "인디애나", "IA": "아이오와",
    "KS": "캔자스", "KY": "켄터키", "LA": "루이지애나", "ME": "메인",
    "MD": "메릴랜드", "MA": "매사추세츠", "MI": "미시간", "MN": "미네소타",
    "MS": "미시시피", "MO": "미주리", "MT": "몬태나", "NE": "네브래스카",
    "NV": "네바다", "NH": "뉴햄프셔", "NJ": "뉴저지", "NM": "뉴멕시코",
    "NY": "뉴욕", "NC": "노스캐롤라이나", "ND": "노스다코타", "OH": "오하이오",
    "OK": "오클라호마", "OR": "오리건", "PA": "펜실베이니아",
    "RI": "로드아일랜드", "SC": "사우스캐롤라이나", "SD": "사우스다코타",
    "TN": "테네시", "TX": "텍사스", "UT": "유타", "VT": "버몬트",
    "VA": "버지니아", "WA": "워싱턴", "WV": "웨스트버지니아",
    "WI": "위스콘신", "WY": "와이오밍",
}


def days_to_election(as_of: "date" = None) -> int:
    return max(0, (ELECTION_DATE - (as_of or date.today())).days)



# ---------------------------------------------------------------------------
# Calibration status
# ---------------------------------------------------------------------------

# CALIBRATION STATUS: UNVALIDATED, and v3 is built so that this costs less
# than it used to.
#
# The historical sample cannot support fitting anything:
#
#   1. data/historical/MANIFEST.md declares the fec_z / trends_z / gdelt_z /
#      youtube_z / adspend_z columns "APPROXIMATE reconstructions". They
#      correlate with outcome at +0.49 to +0.72 — impossibly high for a real
#      signal on a battleground-only sample, and the signature of numbers
#      written knowing the results. The economic columns, which ARE measured
#      (from FRED, point-in-time at election day - 14), correlate at
#      +0.12 / +0.05 / -0.01.
#   2. n=47, and leave-one-cycle-out results swing wildly across the five
#      cycles. That is noise, not a curve with a minimum worth reading off.
#
# Under v2 this was a live hazard: λ multiplied straight into every published
# probability, so an unfittable coefficient sat at the centre of the board.
# v3 does not convert indicators into probabilities at all — the models report
# a lean and a strength, and the only calibrated-looking number left is
# PP_PER_SIGMA, which appears once in a detail view carrying this note.
#
# TO LIFT THIS STATUS: replace the approximate columns with measured ones and
# widen the sample toward the 60-100 competitive House districts per cycle
# that MANIFEST.md asks for. Only then does mapping an indicator to a
# probability become defensible.
CALIBRATION_VALIDATED = False
CALIBRATION_NOTE = ("기초 지표 모델의 눈금(가중치·표준편차→%p 환산)은 과거 데이터로 적합한 값이 아니라 수기 설정값입니다. 쓸 수 있는 과거 표본이 47개뿐이고 그중 상당수가 근사 재구성값이라(data/historical/MANIFEST.md) 적합이 의미를 갖지 못합니다. 그래서 v3는 이 값들로 확률을 만들지 않고 방향과 세기만 보여줍니다.")

# ---------------------------------------------------------------------------
# Board — which races are shown, and in what order (2026-08-08 redesign)
# ---------------------------------------------------------------------------
# The board is built from the betting market's own universe (board.py), ranked
# by trading volume, rather than from a curated race list. See board.py's
# module docstring for the ranking caveat that matters most: on the House,
# cumulative volume ranks SAFE seats highest, because Polymarket lists all 443
# districts and volume accretes with listing age, not with competitiveness.
BOARD = {
    # "volume1wk" = recent attention. "volume" = cumulative since listing.
    #
    # 2026-08-08: default is "volume" at the user's request, so the board
    # matches the order polymarket.com shows when you sort its own Senate list
    # by volume. The caveat above still holds and is not fixed by this choice —
    # on the House, cumulative volume ranks SAFE seats highest. The dashboard
    # says so in the House table header, and offers the other ordering.
    #
    # The recency metric is the WEEK, not the day. A day is too short to mean
    # anything here: Alaska Senate on 2026-08-08 carried $383K cumulative and
    # $90K liquidity but traded $29 in the preceding 24 hours — against $6,459
    # over the week. Ranking on the daily figure reshuffles the board for
    # reasons that have nothing to do with the races.
    "rank_by": "volume",
    # Per chamber. None = no limit.
    "top_n": {"senate": 20, "house": 25, "governor": 20},
}

# Percentage points per standard deviation, for the detail view's `shift_pp`.
#
# READ THIS BEFORE QUOTING THE NUMBER. It is not fitted. It is carried over
# from the old blended engine so the two are traceable to each other: that
# engine used p_alpha = p_consensus + λ·signal with λ = 0.10 and
# signal = weighted_z / z_norm (z_norm = 2.0), i.e. 5 percentage points per
# sigma at the same evidence. The engine is gone; the scale factor is kept
# only so "±X%p" in the detail view is not invented from nothing.
#
# The honest reading of a model is its LEAN and STRENGTH. shift_pp is a
# convenience gloss on the same z-score, and CALIBRATION_VALIDATED (False)
# covers it exactly as it covered λ.
PP_PER_SIGMA = 5.0


# ---------------------------------------------------------------------------
# Race reference data (NOT the race universe — see board.py)
# ---------------------------------------------------------------------------
# The board is built from what the betting market trades, ranked by volume.
# races.json is now an ENRICHMENT lookup: incumbent, rating, past results, and
# the covariates the structural-residual layer needs. A race does not have to
# appear here to be shown, and appearing here does not put it on the board.
#
# "active": false therefore no longer means "hidden". It survives only as a
# marker of which seats the old hand-curated board carried; both dicts are
# merged by pipeline._reference_index(), because reference data is just as
# good for a seat the market trades and we had previously set aside.

with open(os.path.join(REFERENCE_DIR, "races.json"), encoding="utf-8") as _fh:
    _ALL_RACES = json.load(_fh)

# A race with "active": false stays in races.json (metadata, past results and
# covariates are preserved) but drops off the monitored board. Set the flag
# back to true to restore it — that is the whole mechanism.
RACES = {r["race_id"]: r for r in _ALL_RACES if r.get("active", True)}
INACTIVE_RACES = {r["race_id"]: r for r in _ALL_RACES if not r.get("active", True)}

TRACK_A = {
    # Consensus channel weights are TIME-VARYING — see track_a_weights(days_out)
    # above; renormalized over available channels at blend time.

    # Poll margin -> probability: P(Dem) = norm.cdf(margin / sigma).
    "poll_sigma": 6.0,
    # Multiple polls per race: exponential recency decay (days) + sqrt(n) size
    # weighting.
    "poll_halflife_days": 14.0,

    # Kalshi event tickers, hand-verified. Empty by default: 2026 single-race
    # Kalshi markets are illiquid. e.g. {"sen-ga": "KXSENGA-26"}.
    "kalshi_tickers": {},

    # Cache TTL (hours) before a live re-fetch is attempted.
    "cache_ttl_hours": 6,

    # -- Market depth ------------------------------------------------------
    # The Polymarket adapter has always recorded each event's liquidity, and
    # nothing read it. Measured 2026-08-02 across the monitored board it spans
    # roughly $11k to $45k — a four-fold range — yet a $11k Iowa market was
    # being blended at exactly the same weight as a $45k Ohio one.
    #
    # A thin market is weaker evidence: fewer dollars stand behind the price,
    # so a single modest trade moves it further. The betting channel's weight
    # is therefore scaled by
    #
    #     min(1, sqrt(liquidity / liquidity_reference))
    #
    # the same variance-shaped correction the Track B coverage scale uses, for
    # the same reason. Reference is set near the middle of the observed range,
    # so a typical market is barely touched and only genuinely thin ones are
    # discounted. Polls absorb whatever weight the market gives up.
    #
    # This does NOT reach into Track B: it changes how the consensus channels
    # are averaged, which is entirely a Track A concern.
    "liquidity_reference": 25000.0,
    # Never discount below this, however thin — a real market with real money
    # is still a price, and zeroing it would silently make some races
    # poll-only without saying so.
    "liquidity_min_confidence": 0.5,
}

# ===========================================================================
# TRACK B — base signals (alpha). NEVER feeds Track A.
# ===========================================================================

TRACK_B = {
    # Variable weights for the combined signal; renormalized over available
    # variables. Tuned by the backtest, applied by hand.
    #
    # Revised 2026-07-30. The previous set leaned on three sources that are
    # not operable in production — Google Trends via the unofficial pytrends
    # (rate-limited, cannot backfill), X/Twitter (read access is paid) and ad
    # spend (commercial vendors) — so they were replaced with free,
    # documented, behaviour-based sources and the FEC, which was already
    # wired up and emitting one variable out of five it supports.
    "weights": {
        # -- FEC: five variables off the SAME API responses, no extra auth
        #    or quota. Confounders are documented per variable below and
        #    handled by the structural-residual layer (see baseline.py).
        # in-state share is weighted highest of the five: it separates a
        # candidate with a strong NATIONAL fundraising operation from one
        # whose OWN electorate is energized. A Senate candidate can raise the
        # large majority of their money out of state, which makes raw totals
        # a poor proxy for local enthusiasm.
        "fec_in_state_share": 0.2,
        # CONFOUNDER (all five): small-dollar fundraising correlates with
        # ideological EXTREMITY, not only enthusiasm — the most ideologically
        # extreme incumbents raise the most from small donors. Routed through
        # structural_residual with district/state partisanship as the
        # available ideology proxy.
        # CONFOUNDER (all five): competitive races attract disproportionate
        # small-dollar money by construction. The pipeline runs on
        # battlegrounds ONLY, so the cross-sectional baseline is built from
        # other battlegrounds — never from all races. Do not widen the race
        # universe without revisiting this.
        "fec_small_dollar_count": 0.1,
        "fec_unique_donors": 0.1,
        "fec_repeat_donor_rate": 0.08,
        "fec_burn_rate": 0.06,

        # -- Behavioural, fires once per cycle. Actual ballots cast, not
        #    survey responses: this operationalizes the enthusiasm gap the
        #    way the behavioural literature does — as a turnout DIFFERENTIAL
        #    between parties measured from voter records. Survey-based
        #    enthusiasm measures have historically been weak turnout
        #    predictors, which is why this is Track B and not Track A.
        "primary_turnout_ratio": 0.25,
        # ZEROED 2026-08-02. Audited that day: this variable produced a value
        # in ZERO of 33 races. Forty-four states do not register voters by
        # party at all (24 races, structural), and the six that do
        # (AK/IA/ME/NC/NE/NH — 9 races) have no data because
        # party_registration.xlsx was never filled in. So 0.11 of the weight
        # budget sat on a variable that contributed nothing anywhere, while
        # still counting in reference_variable_count() against those 9 races'
        # coverage scale. Exactly the GDELT/Reddit/YouTube situation, and the
        # same remedy. The loader, adapter and template all remain: drop a
        # filled party_registration.xlsx in and restore a weight here.
        "party_reg_net_change": 0.0,

        # -- Fundamentals. Close to ORTHOGONAL to everything else here, which
        #    is the point: the rest of Track B measures enthusiasm, and
        #    enthusiasm variables correlate with one another, so a sixth one
        #    adds less than it appears to. These also work for GOVERNOR races,
        #    where the FEC block above is structurally unavailable and left
        #    the board with no directional signal at all. They took GDELT's
        #    0.14 exactly, so nothing else moved and the backtest can price
        #    the swap in isolation.
        # Claims takes the largest share of the three: it is the only WEEKLY
        # one, so it is the only one that yields a real observation per
        # backfill window instead of repeating a month's value.
        "econ_claims": 0.08,
        "econ_coincident": 0.07,
        "econ_unemployment": 0.03,

        # -- Media / social
        # GDELT ZEROED 2026-08-02, not deleted. Its DOC API answers 429 to
        # essentially every request from us, and the 429 body itself says
        # high-traffic users must move to the bulk ngrams/CSV exports —
        # meaning the failure is our access pattern, not an outage. Rebuild it
        # against data.gdeltproject.org's bulk files (no rate limit,
        # backfillable to 2015) and restore the weight then. Left at 0.0 so
        # the adapter, its cache and its tests stay exercised.
        "gdelt": 0.0,      # media volume/tone deviation — see note above
        # REDDIT AND YOUTUBE ZEROED 2026-08-02. Both need credentials the
        # project has decided not to obtain (Reddit OAuth, which additionally
        # requires pre-approval under the November 2025 Responsible Builder
        # Policy; a YouTube Data API key). Leaving weight on a variable that
        # can never fire is not harmless: reference_variable_count() counts
        # every directional, positively-weighted variable, so each dead one
        # inflates n_reference and shrinks the sqrt(n/N) coverage scale of
        # EVERY race for evidence that does not exist. Zeroing them lifted the
        # reference count from 12 to 10 and the adapters stay wired, so
        # restoring a weight is the only step needed if keys ever arrive.
        "reddit": 0.0,    # state-subreddit mention volume + comment sentiment
        "youtube": 0.0,   # channel-enthusiasm proxies

        # -- ATTENTION-ONLY (never vote a D/R direction; see below).
        "wiki_pageviews_share": 0.02,
        "wiki_edit_count": 0.01,
    },

    # Variables excluded from any directional (D/R) computation; they enter
    # only as attention/interest context.
    #
    # DO NOT "optimize" these back into the directional path. A pageview
    # reveals interest, not vote intent, exactly as a search query does — a
    # candidate's article traffic spikes on a scandal just as it does on a
    # surge of support. This constraint is carried over verbatim from the
    # Google Trends variable these replaced.
    "attention_only": ["wiki_pageviews_share", "wiki_edit_count"],

    # Korean display names emitted into signals[] for the UI.
    "display_names": {
        "fec_small_dollar_count": "소액기부 건수(FEC)",
        "fec_unique_donors": "고유 기부자 수(FEC)",
        "fec_in_state_share": "주내 기부 비중(FEC)",
        "fec_repeat_donor_rate": "재기부율(FEC)",
        "fec_burn_rate": "자금 소진율(FEC)",
        "primary_turnout_ratio": "예비선거 투표율 격차",
        "party_reg_net_change": "정당 등록 순변동",
        "econ_claims": "주 실업수당 청구(주간)",
        "econ_coincident": "주 경기동행지수",
        "econ_unemployment": "주 실업률",
        "gdelt": "미디어 노출·톤",
        "reddit": "주 서브레딧 감성",
        "youtube": "채널 열의",
        "wiki_pageviews_share": "위키 조회 집중도",
        "wiki_edit_count": "위키 편집 활동",
    },

    # Rolling-baseline parameters (per race, per source, per channel).
    "baseline_window": 8,      # observations kept in the rolling window
    "baseline_min_obs": 4,     # fewer than this => z-score is None, not wild
    # Once-per-cycle variables cannot reach 4 observations: their baseline is
    # prior ELECTION cycles, not prior weeks. Three is thin and the resulting
    # z-score is noisy — that is the price of having the variable at all, and
    # it is why both carry modest weight.
    "baseline_min_obs_overrides": {
        "primary_turnout_ratio": 3,
        "party_reg_net_change": 3,
    },
    "z_clip": 3.0,             # one anomalous outlet cannot dominate
    # z -> signal normalization: signal contribution = clip(z)/z_norm, so a
    # 2σ consensus across variables saturates the signal near ±1.
    "z_norm": 2.0,

    # YouTube Data API quota budget (units/UTC day). Search costs 100/call.
    "youtube_daily_budget": 8000,

    "cache_ttl_hours": 12,

    # -- State economics (FRED, no API key) --------------------------------
    # Monthly series, so these are counted in MONTHS, not weeks. 24 months of
    # baseline covers a full business-cycle turn without letting the 2020
    # shock dominate a 2026 reading forever.
    "econ_baseline_months": 24,
    "econ_backfill_months": 18,
    # Claims are weekly: two years of weekly YoY readings.
    "econ_claims_baseline_weeks": 104,

    # -- FEC ---------------------------------------------------------------
    # Itemized Schedule A receipts only become queryable after a committee
    # FILES its report and the FEC loads it. Measured 2026-07-27: the newest
    # itemized receipt for Ossoff's committee was 2026-04-29 — an 89-day lag,
    # even though the July Quarterly (coverage through 06-30) was filed 07-15.
    # So "small-dollar receipts in the last 7 days" is ALWAYS zero and cannot
    # be the observation window. The adapter instead walks back to the newest
    # week that actually has data (the "frontier") and reports that week.
    # Filing schedules differ per committee (monthly vs quarterly), so the
    # frontier is discovered per race, not assumed globally.
    "fec_frontier_start_lag_days": 45,   # start probing this far back
    "fec_frontier_lookback_weeks": 20,   # give up beyond this
    "fec_frontier_ttl_hours": 168,       # re-discover weekly
    "fec_max_candidates": 2,             # per party per race — caps API cost
    "fec_max_committees": 1,             # per candidate
    "fec_small_dollar_max": 200,         # USD ceiling for "small-dollar"
    "fec_min_interval": 1.05,            # seconds; api.data.gov allows ~60/min

    # SAMPLING. fec_small_dollar_count comes from pagination.count and is
    # exact. The four RATIO variables (in-state share, unique donors, repeat
    # rate) need the receipt rows themselves, and a single week for a large
    # Senate committee runs to thousands of rows (measured 2026-07-27:
    # Ossoff's committee, 6,986 rows in one week). So the rows are SAMPLED:
    # the first fec_sample_pages pages, newest first.
    #
    # This biases the sample toward the end of the window — giving is bursty
    # around email blasts. The bias is tolerable because it is CONSTANT: the
    # same rule runs every week, every variable is scored as a deviation from
    # that same race's own history collected the same way, and a constant
    # offset cancels in a z-score. It would NOT be tolerable if these values
    # were ever compared across races at absolute level. They are not.
    "fec_sample_pages": 3,               # x per_page = rows sampled per side
    "fec_sample_per_page": 100,          # FEC's maximum

    # CONDUITS. ActBlue and WinRed must itemize EVERY contribution they
    # process, whereas an ordinary committee only discloses donors above the
    # $200 cycle threshold — so conduit-routed receipts make small-dollar
    # giving far more observable, and are preferred where the data
    # distinguishes them. Measured 2026-07-27: 100/100 sampled sub-$200
    # itemized receipts for Ossoff's committee carried contributor_id
    # C00401224 (ActBlue) and receipt_type "EARMARKED CONTRIBUTION".
    # Detection is by receipt type rather than by hard-coded id so a
    # conduit we have not listed still counts.
    "fec_conduit_ids": {"C00401224": "ActBlue", "C00694323": "WinRed"},
    # Below this conduit share of the sample, fall back to all itemized
    # individual receipts and say so — a committee that does not use a
    # conduit is not a committee with no donors.
    "fec_conduit_min_share": 0.25,

    # -- GDELT -------------------------------------------------------------
    # The DOC API answers 429 with "limit requests to one every 5 seconds",
    # but measured 2026-07-27 it keeps refusing well past that after any
    # burst, so the interval is set far above the advertised floor and the
    # retry ladder is longer. Backfilled series are cached to disk, so a run
    # that still gets throttled out resumes instead of restarting.
    # Measured over a full backfill: at 12s most calls still drew a 429 and
    # were rescued by the retry ladder, costing 60-90s each and sometimes
    # stalling for five minutes. Waiting longer up front is strictly cheaper
    # than retrying — 30s lands the first attempt and halves wall-clock time.
    "gdelt_min_interval": 30.0,
    "gdelt_retries": 4,

    # -- Wikimedia pageviews (replaced Google Trends 2026-07-30) -----------
    # REST pageviews API: free, no auth, generous limits (documented ceiling
    # 200 req/s; we stay far under). The decisive advantage over pytrends is
    # that it BACKFILLS reliably — daily counts are available from 2015 — so
    # this is the first Track B variable that can be measured retroactively
    # at scale, which is what lets backtest.py fit λ and weights against it.
    "wiki_min_interval": 0.2,
    "wiki_project": "en.wikipedia",
    "wiki_agent": "user",        # exclude bots/spiders from pageview counts
    # Article titles are resolved through the API (redirects, disambiguated
    # titles, mid-cycle renames) and cached per candidate. An unresolved
    # candidate FAILS LOUDLY rather than returning zero views — a silent zero
    # would read as "nobody is looking at this candidate", which is a signal.
    "wiki_title_ttl_hours": 720,

    # -- Reddit (replaced the Twitter stub 2026-07-30) ---------------------
    # Verified 2026-07-30 against Reddit's current Data API terms: OAuth2 is
    # MANDATORY (no anonymous key-only path), the free non-commercial tier
    # allows ~100 queries/minute per OAuth client averaged over a 10-minute
    # window (10 QPM unauthenticated), free use requires pre-approval under
    # the November 2025 Responsible Builder Policy, commercial use is a paid
    # agreement, and ML training on the data is prohibited without a licence.
    # Responses carry X-Ratelimit-Used / -Remaining / -Reset, which the
    # adapter reads and paces against instead of assuming a fixed quota.
    # Reddit also throttles generic User-Agents, so the required
    # platform:app_id:version (by /u/username) form is sent.
    #
    # State subreddits rather than a national feed: local-voter share is far
    # higher, so there is much less national-politics noise, and each
    # subreddit is a well-defined baseline population to de-bias against.
    "reddit_min_interval": 1.2,
    "reddit_comment_limit": 200,
    "reddit_subreddits": {
        "AK": "alaska", "AZ": "arizona", "CA": "california", "CO": "Colorado",
        "FL": "florida", "GA": "Georgia", "IA": "Iowa", "KS": "kansas",
        "ME": "Maine", "MI": "Michigan", "NC": "NorthCarolina",
        "NE": "Nebraska", "NH": "newhampshire", "NJ": "newjersey",
        "NV": "Nevada", "NY": "nyc", "OH": "Ohio", "PA": "Pennsylvania",
        "TX": "texas", "VA": "Virginia", "WA": "Washington",
        "WI": "wisconsin",
    },

    # Hand overrides for candidates whose Wikipedia article the search step
    # cannot find or resolves to the wrong person. The adapter has always read
    # this key and its error message has always named it; the key itself was
    # missing until 2026-08-02, so following that message led nowhere.
    #
    # Map a roster name to the exact article title, or to None to declare
    # "this person has no article". None is NOT the same as zero pageviews —
    # it removes the candidate from the roster, whereas a zero would assert
    # that nobody looked them up. Only minor candidates belong here; if a
    # NOMINEE is unresolvable, fix the roster instead.
    "wiki_titles": {
        # Minor candidates with no English Wikipedia article, confirmed by
        # search on 2026-08-02. Without these four the whole race's wiki
        # variables were dropped, not just the candidate's.
        "Karishma Manzur": None,     # sen-nh
        "Adam Derito": None,         # ho-co8
        "Matthew Maasdam": None,     # ho-mi7
        "David Alfred Pautsch": None,   # ho-ia1
        # gov-ia: no article of his own — the name is a REDIRECT to "2026
        # Iowa gubernatorial election", which the adapter now refuses. He is
        # the Republican NOMINEE, so declaring him article-less leaves that
        # side empty and the race's wiki variables correctly unavailable.
        # Revisit if an article is written.
        "Zach Lahn": None,
    },

    # Candidate rosters the FEC cannot supply. candidate_roster() returns None
    # for governor races because the FEC has no jurisdiction over them, which
    # left all five governor races with NO Wikipedia variables at all.
    #
    # PROVENANCE: taken from each state's Wikipedia election article on
    # 2026-08-02 (which cites the state certifications), NOT from the poll
    # workbook — that is Track A data and the two tracks share no inputs.
    # The same articles independently confirm every date in
    # import_polls.GOVERNOR_PRIMARY_DATES.
    "candidate_overrides": {
        # Nominated: GA runoff 06-16, IA 06-02, NV 06-09, OH 05-05.
        "gov-ga": {"dem": ["Keisha Lance Bottoms"], "rep": ["Rick Jackson"]},
        "gov-ia": {"dem": ["Rob Sand"], "rep": ["Zach Lahn"]},
        "gov-nv": {"dem": ["Aaron Ford (Nevada politician)"],
                   "rep": ["Joe Lombardo"]},
        "gov-oh": {"dem": ["Amy Acton"], "rep": ["Vivek Ramaswamy"]},
        # Wisconsin does not nominate until 08-11, so the Democratic side is
        # the declared field rather than a nominee. Pageview share across an
        # unsettled field measures the SIDE's attention, which is the most the
        # variable can support before a primary — revisit after 08-11.
        "gov-wi": {"dem": ["Sara Rodriguez", "David Crowley (Wisconsin politician)",
                           "Francesca Hong", "Joel Brennan"],
                   "rep": ["Tom Tiffany"]},
    },

    # -- backfill ----------------------------------------------------------
    # Weeks of history `python -m election2026 backfill` reconstructs. Must
    # exceed baseline_min_obs (4) for z-scores to come alive immediately;
    # baseline_window (8) caps what is actually retained.
    "backfill_weeks": 8,
}

# ---------------------------------------------------------------------------
# Structural covariates (hand-maintained; no new data source). Used by
# baseline.structural_residual to predict expected Track B variable levels;
# z-scores then run on the residual. Races absent here fall back to the
# rolling-mean baseline (signal provenance marks which path was used).
#   incumbency: +1 Dem incumbent, -1 Rep incumbent, 0 open
#   partisanship: PVI-style lean in points, positive = Dem
#   media_volume: mainstream coverage index (relative, ~1.0 = typical)
#   population: race population in millions
#
# ⚠️ THIS DICT IS NOW WHAT KEEPS THE CROSS-SECTION HONEST. READ BEFORE ADDING.
#
# The FEC confounder documented at TRACK_B["weights"] says competitive races
# attract disproportionate small-dollar money BY CONSTRUCTION, so the
# cross-sectional fit must be built from other battlegrounds and never from
# all races. Under v2 the race universe was a hand-picked battleground list,
# so that held automatically.
#
# It no longer does. Since 2026-08-08 the board comes from the betting market
# ranked by volume, and on the House that surfaces SAFE seats (board.py
# explains why). Measured that day: 65 races on the board, of which 24 appear
# below — and `structural_residual()` fits over the intersection, i.e. it
# still fits over battlegrounds only. The other 41 fall through to the
# rolling-mean baseline and are marked provenance="rolling".
#
# So the guarantee now rests entirely on the CONTENTS of this dict. Adding
# covariates for a safe seat silently puts it in the regression and breaks the
# confounder control. If you widen it, widen it with battlegrounds.
# ---------------------------------------------------------------------------
STRUCTURAL_COVARIATES = {
    "sen-ga": dict(incumbency=1, partisanship=-1.0, media_volume=1.8, population=11.0),
    "sen-mi": dict(incumbency=0, partisanship=0.5, media_volume=1.5, population=10.0),
    "sen-nc": dict(incumbency=0, partisanship=-1.5, media_volume=1.4, population=10.8),
    "sen-me": dict(incumbency=-1, partisanship=2.0, media_volume=1.2, population=1.4),
    "sen-nh": dict(incumbency=0, partisanship=1.0, media_volume=0.9, population=1.4),
    "sen-oh": dict(incumbency=-1, partisanship=-6.0, media_volume=1.3, population=11.8),
    "sen-tx": dict(incumbency=0, partisanship=-5.5, media_volume=1.6, population=30.5),
    "sen-ia": dict(incumbency=0, partisanship=-6.0, media_volume=0.8, population=3.2),
    "sen-ak": dict(incumbency=-1, partisanship=-8.0, media_volume=0.6, population=0.7),
    "sen-ne": dict(incumbency=-1, partisanship=-13.0, media_volume=0.5, population=2.0),
    "sen-mn": dict(incumbency=0, partisanship=1.5, media_volume=1.0, population=5.7),
    "gov-nv": dict(incumbency=-1, partisanship=-0.5, media_volume=1.1, population=3.2),
    "gov-ga": dict(incumbency=0, partisanship=-1.0, media_volume=1.5, population=11.0),
    "gov-ia": dict(incumbency=0, partisanship=-6.0, media_volume=0.8, population=3.2),
    "gov-oh": dict(incumbency=0, partisanship=-6.0, media_volume=1.3, population=11.8),
    "gov-wi": dict(incumbency=0, partisanship=0.0, media_volume=1.1, population=5.9),
    # Deactivated 2026-08-02 (governor board cut to the five battlegrounds on
    # the poll sheet). Kept so restoring active=true needs no second edit.
    "gov-az": dict(incumbency=1, partisanship=-1.5, media_volume=1.3, population=7.5),
    "gov-mi": dict(incumbency=0, partisanship=0.5, media_volume=1.2, population=10.0),
    "gov-ks": dict(incumbency=0, partisanship=-10.0, media_volume=0.6, population=2.9),
    "gov-me": dict(incumbency=0, partisanship=2.0, media_volume=0.7, population=1.4),
    "gov-fl": dict(incumbency=0, partisanship=-6.5, media_volume=1.7, population=22.6),

    # House districts (the 18 NYT tossups). `partisanship` doubles as the
    # IDEOLOGY covariate for the five FEC variables — small-dollar giving
    # tracks ideological extremity as much as enthusiasm, and district lean
    # is the ideology proxy actually available without a new data source.
    # These are hand-maintained APPROXIMATIONS of district partisan lean in
    # points (positive = Dem); replace with published Cook PVI when to hand.
    # A House district is ~0.77M people and draws far less mainstream
    # coverage than a statewide race, hence the low media_volume.
    "ho-az1": dict(incumbency=0, partisanship=-2.0, media_volume=0.5, population=0.77),
    "ho-az6": dict(incumbency=-1, partisanship=-3.0, media_volume=0.5, population=0.77),
    "ho-ca22": dict(incumbency=-1, partisanship=5.0, media_volume=0.5, population=0.77),
    "ho-co8": dict(incumbency=-1, partisanship=1.0, media_volume=0.5, population=0.77),
    "ho-fl25": dict(incumbency=1, partisanship=6.0, media_volume=0.5, population=0.77),
    "ho-ia1": dict(incumbency=-1, partisanship=-3.0, media_volume=0.4, population=0.77),
    "ho-ia3": dict(incumbency=-1, partisanship=-3.0, media_volume=0.4, population=0.77),
    "ho-mi7": dict(incumbency=-1, partisanship=-2.0, media_volume=0.5, population=0.77),
    "ho-nj7": dict(incumbency=-1, partisanship=-1.0, media_volume=0.6, population=0.77),
    "ho-ny17": dict(incumbency=-1, partisanship=3.0, media_volume=0.7, population=0.77),
    "ho-oh9": dict(incumbency=1, partisanship=-3.0, media_volume=0.4, population=0.77),
    "ho-pa7": dict(incumbency=-1, partisanship=-2.0, media_volume=0.5, population=0.77),
    "ho-pa8": dict(incumbency=-1, partisanship=-4.0, media_volume=0.5, population=0.77),
    "ho-pa10": dict(incumbency=-1, partisanship=-5.0, media_volume=0.5, population=0.77),
    "ho-tx34": dict(incumbency=1, partisanship=3.0, media_volume=0.5, population=0.77),
    "ho-va2": dict(incumbency=-1, partisanship=-2.0, media_volume=0.5, population=0.77),
    "ho-wa3": dict(incumbency=1, partisanship=-5.0, media_volume=0.4, population=0.77),
    "ho-wi3": dict(incumbency=-1, partisanship=-4.0, media_volume=0.4, population=0.77),
}

# ---------------------------------------------------------------------------
# Comment generation
# ---------------------------------------------------------------------------

# LLM comments are OFF unless ELECTION2026_LLM_COMMENTS=1 (see comment.py);
# the rule-based Korean generator is the always-available default.
