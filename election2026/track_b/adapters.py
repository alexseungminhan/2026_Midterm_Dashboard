"""track_b/adapters.py — base-signal sources.

Revised 2026-07-30. Google Trends (unofficial pytrends, rate-limited, cannot
backfill), X/Twitter (paid read access) and ad spend (commercial vendors) were
replaced with free, documented, behaviour-based sources, and the FEC — already
wired up and emitting one variable out of the five its responses support — was
expanded.

    fec_*                   FEC API, five variables off the SAME responses:
                            small-dollar count, unique donors, in-state share,
                            repeat-donor rate, burn rate.
    primary_turnout_ratio   state SOS certified primary results (manual)
    party_reg_net_change    state SOS voter-registration reports (manual)
    gdelt                   GDELT DOC 2.0 — media volume per side. Free.
    reddit                  state subreddits — mention volume + sentiment
    youtube                 YouTube Data API v3, quota-budgeted
    wiki_*                  Wikimedia REST pageviews + edits. ATTENTION-ONLY.

PAYLOAD CONTRACT (uniform across Track B)

    {"dem": {"value": float, "baseline": [floats]?},
     "rep": {"value": float, "baseline": [floats]?},
     "total": {"value": float, "baseline": [...]?}?,      # attention-only
     "oriented": {"value": float, "baseline": [...], "min_obs": int?}?}

`value` is the current observation for that party's side of the race. When
"baseline" is present the z-score is computed from it directly; otherwise
signals.py turns into a reading. Absolute values
NEVER reach the signal — only deviations do.

Two contract extensions, both for variables that are intrinsically ONE number
rather than two sides:

  "oriented"  an already Dem-positive contrast (a turnout RATIO, a
              registration NET change). Splitting these into a dem side and a
              rep side and differencing the two z-scores would halve them for
              no reason and invent a second baseline that does not exist.
  "total"     the attention-only scalar, when it is not simply dem + rep.

UNAVAILABILITY is four-valued, not two. A source returns:

    None                      missing THIS RUN (network, quota, no filing yet)
    unavailable("structural") permanently impossible for this race
    unavailable("pending")    will exist later this cycle, does not yet
    a payload                 available

"Structurally unavailable" and "missing this run" must never be treated the
same: the first is permanent and expected (Texas has no party registration and
never will), the second is a problem worth a warning.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional

from .. import cache, config, manual
from ..adapter import Adapter
from ..baseline import week_windows
from .quota import QuotaBudget

_STATE_NAMES = config.STATE_NAMES


def _fold(text: str) -> str:
    """Casefold and strip accents, so 'González' matches 'Gonzalez'."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text or "")
                   if unicodedata.category(c) != "Mn").lower()


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _surname(name: str) -> str:
    """Last non-suffix token of a name, folded. '' when there is none."""
    parts = [p for p in re.split(r"[\s.,]+", _fold(name)) if p]
    parts = [p for p in parts if p not in _NAME_SUFFIXES]
    return parts[-1] if parts else ""


def unavailable(kind: str, reason: str) -> dict:
    """A payload that says WHY there is no number, instead of just None."""
    assert kind in ("structural", "pending"), kind
    return {"unavailable": kind, "reason": reason}


def is_unavailable(payload) -> bool:
    return isinstance(payload, dict) and "unavailable" in payload


class TrackBAdapter(Adapter):
    track = "track_b"

    # Sources that can reconstruct past weekly observations from their API
    # set this True and implement backfill_series (see backfill.py).
    supports_backfill = False

    # Cache namespace. Defaults to the variable name; sources that emit
    # SEVERAL variables from one set of API responses override it so all of
    # them share one cached pull (see FecBase, WikiBase).
    source: Optional[str] = None

    def __init__(self, mock_table: Optional[dict] = None):
        self._mock = mock_table or {}

    @property
    def cache_source(self) -> str:
        return self.source or self.name

    def _pull_mock(self, race_id, meta):
        block = self._mock.get(race_id, {}).get(self.name)
        return dict(block) if block else None

    # -- historical reconstruction -------------------------------------------
    def observation_period(self, race_id: str, meta: dict) -> Optional[str]:
        """Label (YYYY-MM-DD) of the window a live pull covers.

        Defaults to today, which makes re-running the pipeline twice in one
        day overwrite that day's observation instead of appending a second
        copy of it — without that, a source that has not actually moved would
        quietly shrink its own baseline variance and inflate every later
        z-score. Sources whose data lags (fec) or settles a day late (gdelt,
        wiki) override this with the window they really observed.
        """
        return date.today().isoformat()

    def backfill_series(self, race_id: str, meta: dict,
                        weeks: int) -> Optional[dict]:
        """{period (YYYY-MM-DD): payload} for the `weeks` windows BEFORE the
        one a live pull would report — the history behind today's observation.

        Returning None means this source cannot be reconstructed. The default
        loops over _pull_historical; sources whose API hands back a whole
        series in one call (gdelt, wiki) override this instead.
        """
        period = self.observation_period(race_id, meta)
        if period is None or not self.supports_backfill:
            return None
        anchor = date.fromisoformat(period)
        out = {}
        # +1 and [1:]: skip the live window itself so backfill never writes
        # the observation the next run is about to make.
        for start, end in week_windows(anchor, weeks + 1)[1:]:
            payload = self._pull_historical(race_id, meta, start, end)
            if payload is not None and not is_unavailable(payload):
                out[end.isoformat()] = payload
        return out

    def _pull_historical(self, race_id: str, meta: dict,
                         start: date, end: date) -> Optional[dict]:
        raise NotImplementedError


# ===========================================================================
# FEC — five variables off one set of API responses
# ===========================================================================

class FecBase(TrackBAdapter):
    """Shared FEC plumbing; one subclass per emitted variable.

    Free key from api.data.gov via FEC_API_KEY. Federal races only — returns
    a structural unavailable for governor races (the FEC has no jurisdiction
    there, and never will).

    THE FILING LAG. Itemized Schedule A receipts do not exist in the API until
    a committee files its report AND the FEC loads it. Measured 2026-07-27,
    Ossoff's committee (C00718866): newest itemized receipt 2026-04-29, an
    89-day lag — and its July Quarterly, covering through 06-30 and filed
    07-15, was not yet queryable. "Receipts in the last 7 days" is therefore
    identically zero for every race, which is why this anchors on the
    FRONTIER instead: the newest 7-day window that actually returns receipts.
    Filing cadence differs per committee, so the frontier is discovered per
    race and cached for a week. Weekly variation inside a filed quarter is
    real and is what the signal reads.

    All five variables share the frontier, the committee lookup and the
    sampled receipt rows, so emitting five costs no more API calls than one.
    """

    source = "fec"
    BASE = "https://api.open.fec.gov/v1"
    supports_backfill = True
    metric: str = ""
    _limiter = cache.RateLimiter(config.TRACK_B["fec_min_interval"])

    def is_available(self) -> bool:
        return bool(os.environ.get("FEC_API_KEY"))

    # -- live ----------------------------------------------------------------
    def _pull_live(self, race_id, meta):
        if meta["chamber"] == "governor":
            return unavailable(
                "structural",
                "the FEC has no jurisdiction over state governor races")
        self._last_miss_reason = None
        frontier = self._frontier(race_id, meta)
        if frontier is None:
            # A stated reason means the race genuinely has nothing to
            # measure — report that, not a bare None. A bare None reads
            # downstream as "missing this run", which invites someone to go
            # looking for a fetch bug that is not there. Nebraska is the live
            # example: its Democratic committee is under the $200 itemization
            # threshold, so no itemized receipt exists to read.
            if self._last_miss_reason:
                return unavailable("structural", self._last_miss_reason)
            return None
        return self._sides(race_id, meta, frontier - timedelta(days=6),
                           frontier)

    # Set by _frontier when a lookup fails for a REASON we can state, so
    # _pull_live can distinguish "structurally nothing to measure" from
    # "the request did not come back".
    _last_miss_reason: Optional[str] = None

    def observation_period(self, race_id, meta) -> Optional[str]:
        if meta["chamber"] == "governor":
            return None
        frontier = self._frontier(race_id, meta)
        return frontier.isoformat() if frontier else None

    def _pull_historical(self, race_id, meta, start, end):
        if meta["chamber"] == "governor":
            return None
        return self._sides(race_id, meta, start, end)

    def _sides(self, race_id, meta, start: date, end: date) -> Optional[dict]:
        metrics = self._window_metrics(race_id, meta, start, end)
        if metrics is None:
            return None
        sides = {}
        for party in ("dem", "rep"):
            value = metrics.get(party, {}).get(self.metric)
            if value is None:
                return None
            sides[party] = {"value": float(value)}
        return sides

    # -- the five metrics, computed once per (race, window) -------------------
    def _window_metrics(self, race_id, meta, start: date,
                        end: date) -> Optional[dict]:
        """{"dem": {metric: value}, "rep": {...}} for one 7-day window.

        Cached on disk so the five variable adapters share ONE set of API
        calls: whichever runs first pays, the other four read the cache.
        """
        key = "metrics__%s__%s__%s" % (race_id, start.isoformat(),
                                       end.isoformat())
        cached = cache.read(self.track, self.source, key)
        if cached is not None:
            return cached
        out = {}
        for party, code in (("dem", "DEM"), ("rep", "REP")):
            committees = self._committees(race_id, meta, code)
            if not committees:
                return None
            block = self._party_metrics(committees, meta, start, end)
            if block is None:
                return None
            out[party] = block
        cache.write(self.track, self.source, key, out)
        return out

    def _party_metrics(self, committees: list, meta: dict, start: date,
                       end: date) -> Optional[dict]:
        total_count = 0
        rows: list = []
        for committee_id in committees:
            n, sample = self._receipts(committee_id, start, end)
            if n is None:
                return None
            total_count += n
            rows.extend(sample)

        conduit = [r for r in rows if _is_conduit(r)]
        share = len(conduit) / len(rows) if rows else 0.0
        # Conduits (ActBlue/WinRed) must itemize EVERY contribution they
        # process, while an ordinary committee discloses only donors above the
        # $200 cycle threshold — so conduit rows make small-dollar giving far
        # more observable and are preferred where they exist. A committee that
        # simply does not use a conduit is not a committee without donors, so
        # fall back rather than emit nothing.
        used = conduit if share >= config.TRACK_B["fec_conduit_min_share"] \
            else rows

        block = {
            "small_dollar_count": float(total_count),
            "conduit_share": round(share, 4),
            "n_sampled": len(used),
        }
        if used:
            state = meta["state"]
            in_state = sum(1 for r in used
                           if (r.get("contributor_state") or "").upper() == state)
            block["in_state_share"] = in_state / len(used)

            donors = [_donor_key(r) for r in used]
            unique_rate = len(set(donors)) / len(donors)
            # An estimate: the exact distinct-donor count over the whole
            # window would need every row. Scored only against this race's own
            # history computed the same way, so the estimator's bias cancels.
            block["unique_donors"] = total_count * unique_rate

            repeat = sum(1 for r in used if _is_repeat_donor(r))
            block["repeat_donor_rate"] = repeat / len(used)
        else:
            block["in_state_share"] = None
            block["unique_donors"] = None
            block["repeat_donor_rate"] = None

        burns = [b for b in (self._burn_rate(c) for c in committees)
                 if b is not None]
        block["burn_rate"] = sum(burns) / len(burns) if burns else None
        return block

    # -- frontier discovery ---------------------------------------------------
    def _frontier(self, race_id, meta) -> Optional[date]:
        """Newest 7-day window with itemized receipts on BOTH sides.

        Taking the newest week either side has data would silently poison the
        signal: the two parties' committees file on different schedules, so
        the leading weeks would show real Republican receipts against a
        Democratic zero and read as a Republican surge that is really just a
        missing filing. Measured on sen-ga: the Republican side ran three
        weeks past the Democratic one. The frontier is therefore the OLDER of
        the two sides' frontiers, where both are genuinely observed.

        Cached for fec_frontier_ttl_hours: the frontier only moves when the
        FEC loads a new filing, so re-deriving it every run is wasted calls.
        """
        if meta["chamber"] == "governor":
            return None
        cached = cache.read(self.track, self.source, "%s__frontier" % race_id,
                            max_age_hours=config.TRACK_B["fec_frontier_ttl_hours"])
        if cached is not None:
            self._last_miss_reason = cached.get("reason")
            # A cached MISS counts too. Walking back 20 weeks across both
            # sides' committees to prove a race has no itemized receipts is
            # the single most expensive thing this adapter does, and without
            # caching the negative it would be repaid on every run forever.
            return (date.fromisoformat(cached["frontier"])
                    if cached.get("frontier") else None)

        def remember(found, per_party=None, reason=None):
            self._last_miss_reason = reason
            cache.write(self.track, self.source, "%s__frontier" % race_id,
                        {"frontier": found.isoformat() if found else None,
                         "reason": reason,
                         "per_party": {k: v.isoformat()
                                       for k, v in (per_party or {}).items()}})
            return found

        start = date.today() - timedelta(
            days=config.TRACK_B["fec_frontier_start_lag_days"])
        weeks = week_windows(start, config.TRACK_B["fec_frontier_lookback_weeks"])

        per_party = {}
        for code in ("DEM", "REP"):
            committees = self._committees(race_id, meta, code) or []
            if not committees:
                print("[track_b] fec: %s has no %s committee with receipts "
                      "— source unavailable for this race" % (race_id, code))
                return remember(None, reason=(
                    "no %s committee has filed receipts for this seat" % code))
            for _, end in weeks:
                if any(self._count(c, end - timedelta(days=6), end)
                       for c in committees):
                    per_party[code] = end
                    break
            if code not in per_party:
                print("[track_b] fec: %s %s has no itemized receipts in the "
                      "last %d weeks — source unavailable for this race"
                      % (race_id, code,
                         config.TRACK_B["fec_frontier_lookback_weeks"]))
                # A committee only has to itemize contributions once a donor
                # passes $200 for the cycle, so a candidate small enough to
                # stay under that threshold discloses NOTHING itemized. That
                # is a fact about the candidate, not a fetch that failed, and
                # the two must not be reported the same way.
                return remember(None, reason=(
                    "the %s candidate's committee has no itemized receipts in "
                    "the last %d weeks — below the $200 itemization threshold, "
                    "so there is nothing to measure on that side"
                    % (code, config.TRACK_B["fec_frontier_lookback_weeks"])))

        found = min(per_party.values())
        note = ""
        if per_party["DEM"] != per_party["REP"]:
            note = " (DEM %s / REP %s — held to the older side)" % (
                per_party["DEM"], per_party["REP"])
        print("[track_b] fec: %s data frontier = week ending %s (%d days "
              "behind today)%s"
              % (race_id, found, (date.today() - found).days, note))
        return remember(found, per_party)

    # -- API primitives -------------------------------------------------------
    def _committees(self, race_id, meta, party: str,
                    force: bool = False) -> list:
        """Principal committee ids for a party's serious candidates.

        Two calls, then cached for a week: /candidates/totals/ ranks by money
        raised (a filed-but-dormant candidate raising $0 is not the race, and
        /candidates/search/ has no meaningful ordering — it happily returns
        the $0 filer ahead of the sitting senator), and /candidates/search/
        supplies the principal committee ids the ranking endpoint omits.

        `force=True` bypasses the cache — used once per race to migrate
        pre-2026-07-30 cache entries that lack the candidate-name field.
        """
        key = "%s__committees_%s" % (race_id, party.lower())
        cached = None if force else cache.read(
            self.track, self.source, key,
            max_age_hours=config.TRACK_B["fec_frontier_ttl_hours"])
        if cached is not None:
            return cached.get("committees", [])

        params = {
            "api_key": os.environ["FEC_API_KEY"], "state": meta["state"],
            "office": "S" if meta["chamber"] == "senate" else "H",
            "party": party, "cycle": 2026, "election_year": 2026,
            "is_active_candidate": "true", "per_page": 20,
        }
        if meta.get("district"):
            # Accepts both shapes the pipeline can supply: races.json's
            # "TX-15" and board.py's bare "15" / "AL". The FEC codes an
            # at-large district as "00", not "AL", so ND-AL and friends would
            # otherwise query a district that does not exist and come back
            # empty — indistinguishable from a candidate with no filings.
            district = str(meta["district"]).split("-")[-1].upper()
            params["district"] = "00" if district == "AL" else district.zfill(2)

        ranked = cache.http_get("%s/candidates/totals/" % self.BASE,
                                params=dict(params, sort="-receipts"),
                                timeout=30, limiter=self._limiter)
        if ranked is None or ranked.status_code != 200:
            return []
        order = [c["candidate_id"] for c in ranked.json().get("results", [])
                 if float(c.get("receipts") or 0) > 0]
        if not order:
            cache.write(self.track, self.source, key,
                        {"committees": [], "names": {}})
            return []

        resp = cache.http_get("%s/candidates/search/" % self.BASE,
                              params=params, timeout=30, limiter=self._limiter)
        if resp is None or resp.status_code != 200:
            return []
        by_candidate, names = {}, {}
        for cand in resp.json().get("results", []):
            ids = [c.get("committee_id") or c.get("principal_committee_id")
                   for c in (cand.get("principal_committees") or [])]
            ids = [c for c in ids if c]
            if ids:
                by_candidate[cand["candidate_id"]] = ids
            if cand.get("name"):
                names[cand["candidate_id"]] = cand["name"]

        out, picked = [], []
        for cid in order:
            if cid not in by_candidate:
                continue
            out.extend(by_candidate[cid][:config.TRACK_B["fec_max_committees"]])
            picked.append(cid)
            if len(out) >= config.TRACK_B["fec_max_candidates"]:
                break
        out = out[:config.TRACK_B["fec_max_candidates"]]
        # Candidate NAMES ride along on the same cached response. The
        # Wikipedia and Reddit adapters need to know who is running, and
        # taking that from the poll sheet would be reading Track A data — the
        # two tracks share no inputs, structurally.
        cache.write(self.track, self.source, key,
                    {"committees": out,
                     "names": [names[c] for c in picked if c in names]})
        return out

    def candidate_names(self, race_id, meta, party: str) -> list:
        """['OSSOFF, JON'] for a party's serious candidates, or []."""
        key = "%s__committees_%s" % (race_id, party.lower())
        cached = cache.read(self.track, self.source, key,
                            max_age_hours=config.TRACK_B["fec_frontier_ttl_hours"])
        if cached is None or "names" not in cached:
            # Absent OR written before names rode along (pre-2026-07-30):
            # re-derive once, which rewrites the cache in the new shape.
            self._committees(race_id, meta, party,
                             force=cached is not None)
            cached = cache.read(self.track, self.source, key) or {}
        return list(cached.get("names") or [])

    def _count(self, committee_id: str, start: date,
               end: date) -> Optional[int]:
        """Small-dollar individual receipts for one committee in [start, end].

        per_page=1 — only pagination.count is read, so the payload stays tiny
        however many receipts the window holds. Used by the frontier walk,
        which needs "is there anything here at all" and nothing more.
        """
        key = "%s__%s__%s" % (committee_id, start.isoformat(), end.isoformat())
        cached = cache.read(self.track, self.source, key)
        if cached is not None:
            return cached.get("count")
        resp = cache.http_get(
            "%s/schedules/schedule_a/" % self.BASE,
            params={"api_key": os.environ["FEC_API_KEY"],
                    "committee_id": committee_id,
                    "min_date": start.isoformat(), "max_date": end.isoformat(),
                    "is_individual": "true",
                    "max_amount": config.TRACK_B["fec_small_dollar_max"],
                    "per_page": 1},
            timeout=30, limiter=self._limiter)
        if resp is None or resp.status_code != 200:
            return None
        n = int(resp.json().get("pagination", {}).get("count", 0) or 0)
        cache.write(self.track, self.source, key, {"count": n})
        return n

    def _receipts(self, committee_id: str, start: date,
                  end: date) -> tuple:
        """(exact total count, sampled rows) of small-dollar receipts.

        The count is exact (pagination.count). The rows are a SAMPLE — see
        fec_sample_pages in config.py for why that is sound here and where it
        would not be.
        """
        n = self._count(committee_id, start, end)
        if n is None:
            return None, []
        if n == 0:
            return 0, []
        key = "rows__%s__%s__%s" % (committee_id, start.isoformat(),
                                    end.isoformat())
        cached = cache.read(self.track, self.source, key)
        if cached is not None:
            return n, cached.get("rows", [])

        rows: list = []
        params = {"api_key": os.environ["FEC_API_KEY"],
                  "committee_id": committee_id,
                  "min_date": start.isoformat(), "max_date": end.isoformat(),
                  "is_individual": "true",
                  "max_amount": config.TRACK_B["fec_small_dollar_max"],
                  "per_page": config.TRACK_B["fec_sample_per_page"],
                  "sort": "-contribution_receipt_date"}
        last: dict = {}
        for _ in range(config.TRACK_B["fec_sample_pages"]):
            resp = cache.http_get("%s/schedules/schedule_a/" % self.BASE,
                                  params=dict(params, **last), timeout=45,
                                  limiter=self._limiter)
            if resp is None or resp.status_code != 200:
                break
            body = resp.json()
            page = body.get("results") or []
            rows.extend({k: r.get(k) for k in _RECEIPT_FIELDS} for r in page)
            indexes = (body.get("pagination") or {}).get("last_indexes") or {}
            if not page or not indexes:
                break
            last = {"last_index": indexes.get("last_index"),
                    "last_contribution_receipt_date":
                        indexes.get("last_contribution_receipt_date")}
            if not last["last_index"]:
                break
        if not rows:
            return n, []
        cache.write(self.track, self.source, key, {"rows": rows})
        return n, rows

    def _burn_rate(self, committee_id: str) -> Optional[float]:
        """Cycle disbursements / cash on hand — a campaign-intensity proxy.

        SLOW-MOVING BY CONSTRUCTION: committee totals only change when a new
        report is filed, so this variable holds the same value for every week
        inside a filing period and its rolling baseline can have zero variance
        (z-score None) until a filing crosses. That is honest behaviour, not a
        fault, and is why it carries the lowest of the five FEC weights.
        """
        key = "totals__%s" % committee_id
        cached = cache.read(self.track, self.source, key,
                            max_age_hours=config.TRACK_B["fec_frontier_ttl_hours"])
        if cached is None:
            resp = cache.http_get("%s/committee/%s/totals/"
                                  % (self.BASE, committee_id),
                                  params={"api_key": os.environ["FEC_API_KEY"],
                                          "cycle": 2026, "per_page": 1},
                                  timeout=30, limiter=self._limiter)
            if resp is None or resp.status_code != 200:
                return None
            results = resp.json().get("results") or []
            cached = results[0] if results else {}
            cache.write(self.track, self.source, key, cached)
        spend = cached.get("disbursements")
        cash = cached.get("last_cash_on_hand_end_period")
        if spend is None or not cash:
            return None
        return float(spend) / float(cash)


_RECEIPT_FIELDS = ("contributor_name", "contributor_state", "contributor_zip",
                   "contributor_id", "contribution_receipt_amount",
                   "contributor_aggregate_ytd", "receipt_type_desc")


def _is_conduit(row: dict) -> bool:
    """Was this receipt routed through a conduit (ActBlue/WinRed)?

    Detected by receipt type first so a conduit we have not listed still
    counts; the named ids are a secondary check and documentation.
    """
    if "EARMARK" in (row.get("receipt_type_desc") or "").upper():
        return True
    return (row.get("contributor_id") or "") in config.TRACK_B["fec_conduit_ids"]


def _donor_key(row: dict) -> str:
    """Identity of a donor, for distinct-donor counting.

    Name plus the 5-digit ZIP: the FEC has no donor id for individuals, and
    name alone collides across the country ("SMITH, JOHN").
    """
    return "%s|%s" % ((row.get("contributor_name") or "").strip().upper(),
                      (row.get("contributor_zip") or "")[:5])


def _is_repeat_donor(row: dict) -> bool:
    """Had this donor already given this cycle before this contribution?

    contributor_aggregate_ytd is the FEC's own cycle-to-date total for the
    donor INCLUDING this receipt, so an aggregate meaningfully above the
    receipt means earlier giving. The cent of slack absorbs rounding.
    """
    ytd = row.get("contributor_aggregate_ytd")
    amount = row.get("contribution_receipt_amount")
    if ytd is None or amount is None:
        return False
    return float(ytd) > float(amount) + 0.01


def _fec_variable(variable: str, metric: str):
    return type("Fec_%s" % metric, (FecBase,),
                {"name": variable, "metric": metric})


FecSmallDollarCount = _fec_variable("fec_small_dollar_count",
                                    "small_dollar_count")
FecUniqueDonors = _fec_variable("fec_unique_donors", "unique_donors")
FecInStateShare = _fec_variable("fec_in_state_share", "in_state_share")
FecRepeatDonorRate = _fec_variable("fec_repeat_donor_rate",
                                   "repeat_donor_rate")
FecBurnRate = _fec_variable("fec_burn_rate", "burn_rate")


# ===========================================================================
# Primary turnout by party — the strongest single new signal
# ===========================================================================

class PrimaryTurnoutAdapter(TrackBAdapter):
    """Votes cast in the Democratic primary / votes cast in the Republican
    primary, as a deviation from that state's ratio in prior midterm cycles.

    This is ACTUAL BALLOTS CAST rather than survey responses. It
    operationalizes the enthusiasm gap the way the behavioural
    political-science literature does — as a turnout differential between
    parties measured from voter records, which varies in both size and
    direction across elections. Survey-based enthusiasm measures have
    historically been weak predictors of turnout, which is exactly why this
    belongs in Track B and not in Track A.

    Source is state Secretary of State certified results. There is no API and
    none is needed: this fires ONCE PER CYCLE, not daily. It is a static
    manual input (data/manual/primary_turnout.xlsx).

    Two cases emit an explicit reason rather than a coerced number:

      * ALASKA runs a top-four jungle primary with ranked-choice voting.
        Every voter receives the same ballot, so a "Democratic primary
        electorate" does not exist as a quantity to count. Structural.
      * AN UNCONTESTED PRIMARY on one side depresses that side's turnout for
        reasons that have nothing to do with enthusiasm — nobody queues to
        ratify a sole candidate. Detected from contested_dem / contested_rep
        in the template.
    """

    name = "primary_turnout_ratio"

    # Monitored states whose 2026 primary has not been held yet. Michigan
    # votes 2026-08-04; until then the variable is PENDING, not missing.
    PENDING_STATES = {"MI": date(2026, 8, 4)}

    def __init__(self, mock_table: Optional[dict] = None):
        super().__init__(mock_table)
        self._loaded: Optional[dict] = None
        self._changes: Optional[dict] = None

    def _rows(self) -> dict:
        if self._loaded is None:
            self._loaded = manual.load_primary_turnout()  # loud on bad data
        return self._loaded

    def _change_rows(self) -> dict:
        if self._changes is None:
            self._changes = manual.load_primary_turnout_change()
        return self._changes

    def is_available(self) -> bool:
        try:
            return bool(self._rows()) or bool(self._change_rows())
        except manual.ManualDataError:
            raise
        except Exception:
            return False

    def _from_change_table(self, state: str) -> Optional[dict]:
        """Payload from the RELATIVE table, z-scored across states.

        The relative table carries one observation per state, so there is no
        per-state history to deviate from and the baseline has to be the
        cross-section: how far this state's D-vs-R turnout swing sits from the
        swing the other reporting states recorded in the same cycle.

        That is a DIFFERENT question from the one the vote-count path answers.
        It asks "unusual among states this cycle" rather than "unusual for
        this state historically", so a state with a permanently lopsided
        primary electorate is judged on its MOVEMENT, not its level — which is
        the intended reading — but a cycle where every state swings the same
        way registers as no signal anywhere. Stated here rather than
        discovered later. The vote-count table wins whenever it has the
        history, precisely because a state's own past is the better baseline.
        """
        rows = self._change_rows()
        entry = rows.get(state)
        if entry is None:
            return None
        dem, rep = entry["dem_pct"], entry["rep_pct"]
        if not dem or not rep or dem <= 0 or rep <= 0:
            return None
        # Compare the two parties' growth to EACH OTHER, in this state, and
        # stop there (2026-08-09). The cross-state z-score it replaces asked
        # "unusual among the states reporting this cycle", which is a real
        # question but not the one on the card — and it meant Maine's reading
        # moved when Ohio's numbers arrived.
        return {"dem": {"value": float(dem)}, "rep": {"value": float(rep)},
                "detail": "%d turnout as %% of %d: D %.0f%% / R %.0f%%"
                          % (entry["cycle"], entry["prior_cycle"], dem, rep)}

    def _pull_live(self, race_id, meta):
        state = meta["state"]
        if state in manual.NON_PARTISAN_PRIMARY_STATES:
            return unavailable(
                "structural",
                "%s: %s" % (state, manual.NON_PARTISAN_PRIMARY_STATES[state]))
        if state in self.PENDING_STATES and \
                date.today() < self.PENDING_STATES[state]:
            return unavailable(
                "pending", "%s's 2026 primary is on %s — no votes cast yet"
                % (state, self.PENDING_STATES[state].isoformat()))

        by_cycle = self._rows().get(state)
        if not by_cycle:
            return self._from_change_table(state)
        current = by_cycle.get(2026)
        if current is None:
            return self._from_change_table(state)
        if current["contested_dem"] is False or current["contested_rep"] is False:
            side = "Democratic" if current["contested_dem"] is False \
                else "Republican"
            return unavailable(
                "structural",
                "%s's 2026 %s primary was uncontested, which depresses that "
                "side's turnout for structural reasons unrelated to "
                "enthusiasm" % (state, side))

        prior = [entry["ratio"] for cycle, entry in sorted(by_cycle.items())
                 if cycle != 2026
                 and entry["contested_dem"] is not False
                 and entry["contested_rep"] is not False]
        if len(prior) < 2:
            # Not enough of this state's own history to deviate from; the
            # cross-sectional table still answers a weaker version of the
            # question, so prefer it to silence.
            return self._from_change_table(state)

        import math
        # Log ratio: a 2x Democratic edge and a 2x Republican edge are then
        # equal and opposite deviations, which a raw ratio does not give.
        return {"oriented": {
            "value": math.log(current["ratio"]),
            "baseline": [math.log(r) for r in prior],
            "min_obs": config.TRACK_B["baseline_min_obs_overrides"].get(
                self.name, config.TRACK_B["baseline_min_obs"]),
            "detail": "2026 D/R = %.3f over %d prior contested cycles"
                      % (current["ratio"], len(prior)),
        }}


# ===========================================================================
# State economic conditions (FRED) — added 2026-08-02
# ===========================================================================

class FredBase(TrackBAdapter):
    """State economic trajectory, as the retrospective-voting literature uses
    it: voters reward or punish the party in power for conditions they can
    feel.

    WHY THIS IS HERE. Every other Track B variable measures ENTHUSIASM, and
    enthusiasm variables correlate with each other — stacking more of them
    adds less than it looks like it does. Economic conditions are close to
    orthogonal to all of them, so they add genuinely independent information.
    They are also the only Track B variable that can be BACKTESTED: the series
    run monthly to 1976, so backtest.py can fit lambda and the weights against
    2016/2018/2020/2022/2024 instead of taking them on faith.

    WHY IT MATTERS MOST FOR GOVERNORS. The FEC has no jurisdiction over state
    races, which stripped governor races of the five variables carrying 0.42
    of the weight and left them with NO directional signal at all. State
    economic data has no such gap — and voters attribute state conditions to a
    governor far more readily than to one member of Congress.

    SOURCE. FRED's CSV endpoint, which needs NO API KEY (verified 2026-08-02:
    fredgraph.csv?id=OHUR returns 200 with 607 monthly observations from
    1976-01 through 2026-06). That keeps it inside the same bar every other
    surviving Track B source had to clear — free, documented, no auth, and
    reconstructable backwards.

    ATTRIBUTION. Improvement has to be credited to somebody, and who that is
    differs by office:

      governor      the party holding the GOVERNORSHIP (seat_party). State
                    economic conditions are attributed to the state executive.
      senate/house  the PRESIDENT's party (config.PRESIDENT_PARTY). The
                    midterm penalty is a referendum on the national
                    administration; a first-term House member is not held to
                    account for the unemployment rate.

    CAVEAT, stated rather than hidden: House districts get their STATE's
    numbers, because no monthly district-level economic series exists. A
    district is not its state, so this variable is weakest exactly where the
    board is densest. It is one reason the two economic variables carry the
    modest weight they do.
    """

    source = "econ"
    supports_backfill = True
    # Two ways in. The official JSON API is preferred because the CSV host
    # times out from GitHub's runners — every request burned 3 retries x 30s
    # there, 2.2 hours of pure waiting across a board, which is what killed
    # the first two cloud runs. The CSV needs no key, so it stays as the
    # fallback for a local checkout that has not set one.
    API = "https://api.stlouisfed.org/fred/series/observations"
    CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    _limiter = cache.RateLimiter(0.5)
    _dead = set()          # series that failed this run; see _series()

    series_suffix = ""        # "UR" (unemployment) / "PHCI" (coincident)
    higher_is_better = False  # unemployment: up = worse
    as_percent_change = False
    # Months differenced to read a trajectory. Three smooths the month-to-month
    # noise without lagging so far that it misses a turn.
    # A YEAR, not a quarter (2026-08-09). The old reading was "the last 3
    # months, z-scored against the same 3-month change over the previous 24" —
    # two nested comparisons that nobody could restate. A year-over-year
    # change is one comparison and says itself: "실업률이 1년 전보다 0.4%p
    # 높다". Twelve months also cancels seasonality without a seasonal model.
    LOOKBACK_MONTHS = 12

    def series_id(self, meta: dict) -> str:
        return "%s%s" % (meta["state"], self.series_suffix)

    def _series(self, meta: dict) -> Optional[dict]:
        """{date (first of month): float}, whole history, cached."""
        sid = self.series_id(meta)
        key = "series__%s" % sid
        cached = cache.read(self.track, self.source, key, max_age_hours=24)
        if cached is not None:
            return {date.fromisoformat(d): v for d, v in cached.items()}

        # FAILURES ARE REMEMBERED FOR THE RUN. FRED refuses connections from
        # GitHub's IP range: every request burns 3 retries x 30s, and without
        # this the same unreachable series is re-attempted once per race —
        # 90 fetches x 90s = 2.2 hours of pure timeout, which is exactly what
        # killed the first cloud runs. One attempt per series, then give up.
        if sid in self._dead:
            return None

        out = self._from_api(sid)
        if out is None:
            out = self._from_csv(sid)
        if not out:
            self._dead.add(sid)
            return None
        cache.write(self.track, self.source, key,
                    {d.isoformat(): v for d, v in out.items()})
        return out

    def _from_api(self, sid: str) -> Optional[dict]:
        """Official JSON API. None when no key is set or the call fails."""
        key = os.environ.get("FRED_API_KEY")
        if not key:
            return None
        resp = cache.http_get(self.API, timeout=20, retries=2,
                              limiter=self._limiter,
                              params={"series_id": sid, "api_key": key,
                                      "file_type": "json"})
        if resp is None or resp.status_code != 200:
            return None
        try:
            rows = resp.json().get("observations", [])
        except Exception:
            return None
        out = {}
        for row in rows:
            value = str(row.get("value", "")).strip()
            if not value or value == ".":      # FRED marks gaps with a dot
                continue
            try:
                out[date.fromisoformat(row["date"])] = float(value)
            except (ValueError, KeyError):
                continue
        return out or None

    def _from_csv(self, sid: str) -> Optional[dict]:
        """Keyless fallback. Blocked from some cloud hosts — see API above."""
        resp = cache.http_get(self.CSV, params={"id": sid}, timeout=15,
                              limiter=self._limiter, retries=1)
        if resp is None or resp.status_code != 200:
            return None
        out = {}
        for line in resp.text.splitlines()[1:]:
            when, _, value = line.partition(",")
            value = value.strip()
            if not value or value == ".":
                continue
            try:
                out[date.fromisoformat(when.strip())] = float(value)
            except ValueError:
                continue
        return out or None

    @staticmethod
    def _incumbent_party(meta: dict) -> str:
        if meta["chamber"] == "governor":
            return meta.get("seat_party") or config.PRESIDENT_PARTY
        return config.PRESIDENT_PARTY

    def _oriented(self, meta: dict, series: dict,
                  months: list, i: int) -> Optional[dict]:
        """Dem-positive economic trajectory ending at months[i]."""
        j = i - self.LOOKBACK_MONTHS
        if j < 0:
            return None
        now, then = series[months[i]], series[months[j]]
        if self.as_percent_change:
            if then == 0:
                return None
            change = (now / then - 1.0) * 100.0
        else:
            change = now - then
        improvement = change if self.higher_is_better else -change
        party = self._incumbent_party(meta)
        value = improvement if party == "D" else -improvement

        # Baseline = this state's OWN distribution of the same quantity, so a
        # structurally volatile state is not read as permanently anomalous.
        # Taken from the months BEFORE this one, never including it.
        history = []
        for k in range(self.LOOKBACK_MONTHS, i):
            a, b = series[months[k]], series[months[k - self.LOOKBACK_MONTHS]]
            if self.as_percent_change:
                if b == 0:
                    continue
                c = (a / b - 1.0) * 100.0
            else:
                c = a - b
            imp = c if self.higher_is_better else -c
            history.append(imp if party == "D" else -imp)
        if len(history) < config.TRACK_B["baseline_min_obs"]:
            return None
        # `direct` means "already a signed, oriented reading — do not z-score
        # it". The history is still computed above because backfill uses the
        # same code path.
        #
        # `value` is ORIENTED (+ = 민주 유리) and `raw_change` is the year-over-
        # year move in the series' own units; they are NOT the same number and
        # the detail view shows the latter. They coincide only by a double
        # negation — a lower-is-better series under a Republican incumbent —
        # which is why the mismatch hid: 실업률/청구 looked right on every
        # Senate race while 경기동행지수, the one higher-is-better series,
        # displayed its sign flipped (Maine +1.564% shown as −1.564%).
        return {"oriented": {
            "value": value,
            "direct": True,
            "raw_change": change,
            "baseline": history[-config.TRACK_B["econ_baseline_months"]:],
            "detail": "%s %+.2f over %d months (credited to %s, the party in "
                      "power for this office)"
                      % (self.series_id(meta), change, self.LOOKBACK_MONTHS,
                         party),
        }}

    def _months(self, series: dict) -> list:
        return sorted(series)

    def observation_period(self, race_id, meta):
        series = self._series(meta)
        if not series:
            return None
        return max(series).isoformat()

    def _pull_live(self, race_id, meta):
        series = self._series(meta)
        if not series:
            return None
        months = self._months(series)
        return self._oriented(meta, series, months, len(months) - 1)

    def backfill_series(self, race_id, meta, weeks):
        """Monthly observations, not weekly.

        backfill.py aligns sources by RECENCY INDEX rather than by calendar
        date precisely so that sources on different clocks can coexist, which
        is what lets this return months where the FEC returns weeks. Eight
        weekly windows would land inside two or three distinct months, repeat
        the same value, and collapse the baseline variance to nearly zero —
        which would inflate every later z-score rather than leave a gap.
        """
        series = self._series(meta)
        if not series:
            return None
        months = self._months(series)
        out = {}
        depth = max(weeks, config.TRACK_B["econ_backfill_months"])
        for i in range(len(months) - 2, len(months) - 2 - depth, -1):
            if i < 0:
                break
            payload = self._oriented(meta, series, months, i)
            if payload is not None:
                out[months[i].isoformat()] = payload
        return out or None


class EconCoincidentAdapter(FredBase):
    """Philadelphia Fed state coincident index — the purpose-built measure of
    state economic activity, bundling payroll employment, hours worked and
    real wages into one series. Broader and steadier than the unemployment
    rate alone, which is why it carries the larger of the two weights."""

    name = "econ_coincident"
    series_suffix = "PHCI"
    higher_is_better = True
    as_percent_change = True


class EconClaimsAdapter(FredBase):
    """State initial unemployment claims — WEEKLY, and the only economic
    series whose clock matches the rest of the pipeline.

    Why it earns the largest of the three economic weights:

      * WEEKLY. The coincident index and the unemployment rate are monthly, so
        eight weekly backfill windows land inside two or three distinct months
        and repeat values. Claims give a genuine observation per window, which
        is what the rolling baseline was built for.
      * BEHAVIOURAL. A claim is a person filing a form, not a survey response
        — the same reason primary turnout outranks polled enthusiasm.
      * It leads. Claims turn before the unemployment rate does, so this reads
        a deteriorating economy months earlier.

    SEASONALITY is the catch, and it is severe: these are NOT seasonally
    adjusted, and July claims spike every year in the manufacturing states
    when auto plants shut down for retooling. Ohio's raw series jumps ~40%
    in the first week of July in a normal year. So the observation is a
    YEAR-OVER-YEAR change of a four-week average: differencing against the
    same weeks last year removes the seasonal pattern without having to model
    it, and the four-week window damps the single-week noise that survives.
    """

    name = "econ_claims"
    series_suffix = "ICLAIMS"
    higher_is_better = False      # more claims = worse economy
    SMOOTH_WEEKS = 4
    YOY_WEEKS = 52

    def _oriented(self, meta, series, months, i):
        """Override: year-over-year change of a 4-week mean, in percent.

        `months` is the sorted week list here — FredBase's helpers are
        frequency-agnostic and only ever index into it.
        """
        def mean_at(end_i):
            lo = end_i - self.SMOOTH_WEEKS + 1
            if lo < 0:
                return None
            vals = [series[months[k]] for k in range(lo, end_i + 1)]
            return sum(vals) / len(vals)

        def yoy(end_i):
            now, prior = mean_at(end_i), mean_at(end_i - self.YOY_WEEKS)
            if now is None or prior is None or prior == 0:
                return None
            return (now / prior - 1.0) * 100.0

        change = yoy(i)
        if change is None:
            return None
        improvement = -change          # claims up = economy down
        party = self._incumbent_party(meta)
        value = improvement if party == "D" else -improvement

        history = []
        for k in range(self.YOY_WEEKS + self.SMOOTH_WEEKS, i):
            c = yoy(k)
            if c is None:
                continue
            imp = -c
            history.append(imp if party == "D" else -imp)
        if len(history) < config.TRACK_B["baseline_min_obs"]:
            return None
        # `direct`, like the two monthly series (2026-08-10). Without it this
        # override fell through to the z-score path while FredBase._oriented
        # did not, so the economy model tallied one variable in sigma and two
        # in their own units. Two consequences, both removed by this flag:
        #
        #   * TOO_SMALL (0.1) meant "0.1 sigma" here and "0.1%p" there. On the
        #     board's 21 states 0.1 sigma worked out to a 1.3% YoY move at the
        #     median but ranged 0.35%-3.1%, so the vote threshold moved with a
        #     state's claims volatility while the other two stayed fixed.
        #   * The z-score asks "unusual FOR THIS STATE", not "better than a
        #     year ago". Alaska's claims fell 9.4% YoY — an improvement — yet
        #     z came out POSITIVE because Alaska's claims usually fall further
        #     than that. One variable answering a different question from the
        #     two beside it, then having its sign counted alongside them.
        #
        # The baseline stays: backfill_series() reuses this method, and a
        # payload that carries its history costs nothing.
        return {"oriented": {
            "value": value,
            "direct": True,
            "raw_change": change,
            "baseline": history[-config.TRACK_B["econ_claims_baseline_weeks"]:],
            "detail": "%s %+.1f%% YoY (4-week mean, credited to %s)"
                      % (self.series_id(meta), change, party),
        }}

    def backfill_series(self, race_id, meta, weeks):
        """Weekly, so the ordinary weekly depth is the right one here."""
        series = self._series(meta)
        if not series:
            return None
        wk = sorted(series)
        out = {}
        for i in range(len(wk) - 2, len(wk) - 2 - max(weeks, 12), -1):
            if i < 0:
                break
            payload = self._oriented(meta, series, wk, i)
            if payload is not None:
                out[wk[i].isoformat()] = payload
        return out or None


class EconUnemploymentAdapter(FredBase):
    """State unemployment rate (BLS LAUS via FRED). Narrower than the
    coincident index and noisier month to month, but it is the number voters
    actually hear quoted, which is the mechanism retrospective voting runs
    through."""

    name = "econ_unemployment"
    series_suffix = "UR"
    higher_is_better = False
    as_percent_change = False


# ===========================================================================
# Party registration net change
# ===========================================================================

class PartyRegistrationAdapter(TrackBAdapter):
    """Net change in registered voters by party over the cycle, as a
    deviation from that state's own historical baseline.

    Only six of the ten monitored states record party at registration:
    Alaska, Maine, New Hampshire, North Carolina, Iowa, Nebraska. Michigan,
    Ohio, Georgia and Texas have NO party registration at all — a permanent
    structural fact, reported as such rather than as a missing reading.

    Cadences differ (North Carolina publishes weekly, others far less often),
    so each observation records its report date and the change is expressed
    as a PER-DAY rate between consecutive reports. Comparing a monthly delta
    against a weekly one as if they were the same window would be nonsense.
    """

    name = "party_reg_net_change"

    def __init__(self, mock_table: Optional[dict] = None):
        super().__init__(mock_table)
        self._loaded: Optional[dict] = None

    def _rows(self) -> dict:
        if self._loaded is None:
            self._loaded = manual.load_party_registration()
        return self._loaded

    def is_available(self) -> bool:
        """Always True — this adapter can answer even with no data file.

        Forty-four states do not register voters by party at all, and that
        answer needs no spreadsheet. Gating on the file's existence made
        Adapter.fetch() skip the adapter entirely, so those races were
        reported as "missing this run" instead of "structurally impossible" —
        which put a variable that can NEVER exist for them into
        reference_variable_count(), shrinking the sqrt(n/N) coverage scale of
        every one. Measured 2026-08-02: 27 of 33 races were penalised this
        way. Same defect as the sen-ne FEC case, same fix.

        A ManualDataError still propagates: malformed data must fail loudly.
        """
        try:
            self._rows()
        except manual.ManualDataError:
            raise
        except Exception:
            pass
        return True

    def _pull_live(self, race_id, meta):
        state = meta["state"]
        if state not in manual.PARTY_REGISTRATION_STATES:
            return unavailable(
                "structural",
                "%s does not register voters by party (only %s do)"
                % (state, ", ".join(sorted(manual.PARTY_REGISTRATION_STATES))))
        rows = self._rows().get(state) or []
        if len(rows) < 4:
            return None
        rates = []
        for prev, cur in zip(rows, rows[1:]):
            days = (date.fromisoformat(cur["report_date"])
                    - date.fromisoformat(prev["report_date"])).days
            if days <= 0:
                continue
            net_now = cur["dem_registered"] - cur["rep_registered"]
            net_prev = prev["dem_registered"] - prev["rep_registered"]
            rates.append((net_now - net_prev) / days)
        if len(rates) < 3:
            return None
        return {"oriented": {
            "value": rates[-1],
            "baseline": rates[:-1],
            "min_obs": config.TRACK_B["baseline_min_obs_overrides"].get(
                self.name, config.TRACK_B["baseline_min_obs"]),
            "detail": "net D-R registrations/day, %s vs %d prior windows"
                      % (rows[-1]["report_date"], len(rates) - 1),
        }}


# ===========================================================================
# GDELT — media volume per side, deviation vs own baseline. Free, no auth.
# ===========================================================================

class WikiBase(TrackBAdapter):
    """Wikimedia REST pageviews + edit counts. Free, no auth.

    ATTENTION-ONLY, both variables. They feed the attention measure and must
    NEVER contribute to the directional (D/R) determination — a pageview
    reveals interest, not vote intent, exactly as a search query does. This
    constraint is inherited verbatim from the Google Trends variable these
    replaced; see config.TRACK_B["attention_only"]. Do not "optimize" it away.

    The important secondary benefit over pytrends: this API BACKFILLS
    reliably, with daily counts available from 2015. That makes these the
    first Track B variables measurable retroactively at scale, which is what
    lets backtest.py fit λ and the weights against real historical attention
    rather than against nothing (see backtest.wiki_history).

    TITLE RESOLUTION is the fragile part — candidates have disambiguated
    titles ("Jon Ossoff" vs "John Smith (politician)"), redirects, and
    mid-cycle renames. Titles are resolved through the MediaWiki API, cached
    per candidate, and an unresolved candidate FAILS LOUDLY. Returning zero
    views for a name we could not find would read as "nobody is looking at
    this candidate", which is itself a signal — a false one.
    """

    source = "wiki"
    API = "https://en.wikipedia.org/w/api.php"
    REST = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            "%s/all-access/%s/%s/daily/%s/%s")
    supports_backfill = True
    _limiter = cache.RateLimiter(config.TRACK_B["wiki_min_interval"])

    def observation_period(self, race_id, meta) -> Optional[str]:
        # Pageview dumps settle a day late.
        return (date.today() - timedelta(days=1)).isoformat()

    def _pull_live(self, race_id, meta):
        end = date.today() - timedelta(days=1)
        return self._window(race_id, meta, end - timedelta(days=6), end)

    def backfill_series(self, race_id, meta, weeks):
        """Free history: one REST call per candidate covers every week."""
        anchor = date.fromisoformat(self.observation_period(race_id, meta))
        windows = week_windows(anchor, weeks + 1)[1:]
        oldest = min(start for start, _ in windows)
        series = self._series(race_id, meta, oldest, anchor)
        if series is None:
            return None
        out = {}
        for start, end in windows:
            payload = self._reduce(series, start, end)
            if payload is not None:
                out[end.isoformat()] = payload
        return out

    def _window(self, race_id, meta, start, end):
        series = self._series(race_id, meta, start, end)
        if series is None:
            return None
        return self._reduce(series, start, end)

    # -- per-variable reduction ----------------------------------------------
    def _reduce(self, series: dict, start: date, end: date) -> Optional[dict]:
        raise NotImplementedError

    # -- shared fetch ---------------------------------------------------------
    def _series(self, race_id, meta, start: date, end: date) -> Optional[dict]:
        """{"dem": {date: (views, edits)}, "rep": {...}} summed per side."""
        titles = self._titles(race_id, meta)
        if titles is None:
            return None
        out = {}
        for party, per_side in titles.items():
            merged: dict = {}
            for title in per_side:
                views = self._pageviews(title, start, end)
                edits = self._edits(title, start, end)
                if views is None:
                    return None
                for day, n in views.items():
                    v, e = merged.get(day, (0.0, 0.0))
                    merged[day] = (v + n, e + (edits or {}).get(day, 0.0))
            if not merged:
                return None
            out[party] = merged
        return out

    def _titles(self, race_id, meta) -> Optional[dict]:
        key = "titles__%s" % race_id
        cached = cache.read(self.track, self.source, key,
                            max_age_hours=config.TRACK_B["wiki_title_ttl_hours"])
        if cached is not None:
            return cached.get("titles") or None
        roster = candidate_roster(race_id, meta)
        if not roster:
            return None
        overrides = config.TRACK_B.get("wiki_titles", {})
        titles = {}
        for party, names in roster.items():
            resolved = []
            for name in names:
                # An explicit None override declares "this person has no
                # article" — drop them from the roster. That is a different
                # claim from an unresolved name, and only the latter is a gap
                # in our knowledge.
                if name in overrides and overrides[name] is None:
                    continue
                title = self._resolve(name, meta)
                if title is None:
                    # SKIP, do not fail the race. Dropping the whole contest
                    # over one unresolvable minor name cost 28 of 40 races on
                    # 2026-08-08 — Ohio was blanked because 'Frederick J Ode'
                    # has no article, while Sherrod Brown and Jon Husted both
                    # resolve fine.
                    #
                    # Skipping is not the same as reporting zero. A zero would
                    # say "nobody looked this person up", inflating the other
                    # side; leaving them out says "not measured", and the
                    # share is then taken over the candidates we could measure
                    # on each side. The guard that actually matters is below:
                    # a side with nobody left is still fatal.
                    print("[track_b] wiki: %s — no Wikipedia article for %r; "
                          "measuring the rest of the field. Add the title to "
                          "config.TRACK_B['wiki_titles'] to include them, or "
                          "map to None to silence this."
                          % (race_id, name))
                    continue
                if title not in resolved:      # two candidates, one article
                    resolved.append(title)
            # A side with nobody left is not measurable: a pageview SHARE
            # needs both sides. Fail the race rather than compute a share
            # against an empty denominator.
            if not resolved:
                print("[track_b] wiki: %s — no %s candidate has an article, "
                      "so the D-vs-R pageview share has no %s side to "
                      "measure." % (race_id, party, party))
                return None
            titles[party] = resolved
        cache.write(self.track, self.source, key, {"titles": titles})
        return titles

    # A candidate with no article of their own is often a REDIRECT to the
    # election article — measured 2026-08-02, "Zach Lahn" resolved to "2026
    # Iowa gubernatorial election". Following that redirect would score the
    # Republican channel with traffic the two sides generate jointly, which is
    # worse than having no reading: it is a reading of the wrong thing that
    # looks like a reading of the right one. Titles matching these patterns
    # are therefore treated as unresolved.
    # A DISAMBIGUATION page is the worst kind of wrong match: it exists, it
    # resolves, and it carries almost no traffic. Maine resolved its
    # Republican side to "Susan Collins (disambiguation)" on 2026-08-08 and
    # scored her at ~0 views against the real article's 15,914 for the week,
    # which put the Democratic pageview share at 99.8% in a race the market
    # had at 67.8%. The reading looked like a landslide and was an artefact.
    _NOT_A_PERSON = re.compile(
        r"(gubernatorial|senate|congressional|presidential|house of "
        r"representatives) election|^List of |^\d{4} United States"
        r"|\(disambiguation\)", re.I)

    def _resolve(self, name: str, meta: dict) -> Optional[str]:
        """Canonical article title for a candidate, following redirects.

        Hand overrides win, then an exact title lookup with redirect
        resolution, then a scoped search. Never guesses a title by string
        munging — a wrong title silently measures a different person.
        """
        override = config.TRACK_B.get("wiki_titles", {}).get(name)
        if override:
            return override

        resp = cache.http_get(self.API, params={
            "action": "query", "titles": name, "redirects": 1,
            "format": "json", "formatversion": 2}, timeout=20,
            limiter=self._limiter)
        if resp is not None and resp.status_code == 200:
            pages = (resp.json().get("query") or {}).get("pages") or []
            for page in pages:
                title = page.get("title")
                if not page.get("missing") and title \
                        and not self._NOT_A_PERSON.search(title):
                    return title

        state = _STATE_NAMES.get(meta["state"], meta["state"])
        resp = cache.http_get(self.API, params={
            "action": "query", "list": "search",
            "srsearch": "%s politician %s" % (name, state),
            "srlimit": 1, "format": "json", "formatversion": 2}, timeout=20,
            limiter=self._limiter)
        if resp is None or resp.status_code != 200:
            return None
        hits = ((resp.json().get("query") or {}).get("search") or [])
        for hit in hits:
            title = hit["title"]
            if self._NOT_A_PERSON.search(title):
                continue
            # The search NEVER returns nothing — it returns the closest page
            # it has, which for a candidate with no article is some unrelated
            # one. Measured 2026-08-02, the unguarded top hit mapped a Texas
            # Democrat to "Deaths in December 2025" and a Michigan REPUBLICAN
            # to "Gretchen Whitmer", a high-traffic Democrat, silently loading
            # her pageviews into the Republican channel. Requiring the
            # surname to survive into the title is the cheap check that keeps
            # a legitimate disambiguation ("Aaron Ford" -> "Aaron Ford
            # (Nevada politician)") while rejecting a different person.
            if _surname(name) and _surname(name) not in _fold(title):
                continue
            return title
        return None

    def _pageviews(self, title: str, start: date,
                   end: date) -> Optional[dict]:
        from urllib.parse import quote
        key = "views__%s__%s__%s" % (_slug(title), start.isoformat(),
                                     end.isoformat())
        cached = cache.read(self.track, self.source, key,
                            max_age_hours=config.TRACK_B["cache_ttl_hours"])
        if cached is not None:
            return {date.fromisoformat(d): v for d, v in cached.items()}
        url = self.REST % (config.TRACK_B["wiki_project"],
                           config.TRACK_B["wiki_agent"],
                           quote(title.replace(" ", "_"), safe=""),
                           start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        resp = cache.http_get(url, timeout=30, limiter=self._limiter)
        if resp is None or resp.status_code != 200:
            return None
        out = {}
        for item in resp.json().get("items") or []:
            stamp = str(item.get("timestamp") or "")[:8]
            try:
                out[date(int(stamp[:4]), int(stamp[4:6]),
                         int(stamp[6:8]))] = float(item.get("views") or 0)
            except ValueError:
                continue
        cache.write(self.track, self.source, key,
                    {d.isoformat(): v for d, v in out.items()})
        return out

    def _from_api(self, sid: str) -> Optional[dict]:
        """Official JSON API. None when no key is set or the call fails."""
        key = os.environ.get("FRED_API_KEY")
        if not key:
            return None
        resp = cache.http_get(self.API, timeout=20, retries=2,
                              limiter=self._limiter,
                              params={"series_id": sid, "api_key": key,
                                      "file_type": "json"})
        if resp is None or resp.status_code != 200:
            return None
        try:
            rows = resp.json().get("observations", [])
        except Exception:
            return None
        out = {}
        for row in rows:
            value = str(row.get("value", "")).strip()
            if not value or value == ".":      # FRED marks gaps with a dot
                continue
            try:
                out[date.fromisoformat(row["date"])] = float(value)
            except (ValueError, KeyError):
                continue
        return out or None

    def _from_csv(self, sid: str) -> Optional[dict]:
        """Keyless fallback. Blocked from some cloud hosts — see API above."""
        resp = cache.http_get(self.CSV, params={"id": sid}, timeout=15,
                              limiter=self._limiter, retries=1)
        if resp is None or resp.status_code != 200:
            return None
        out = {}
        for line in resp.text.splitlines()[1:]:
            when, _, value = line.partition(",")
            value = value.strip()
            if not value or value == ".":
                continue
            try:
                out[date.fromisoformat(when.strip())] = float(value)
            except ValueError:
                continue
        return out or None

    def _edits(self, title: str, start: date, end: date) -> Optional[dict]:
        """{date: edit count} for an article — active engagement, not passive
        attention, which is why it is a separate variable from pageviews."""
        key = "edits__%s__%s__%s" % (_slug(title), start.isoformat(),
                                     end.isoformat())
        cached = cache.read(self.track, self.source, key,
                            max_age_hours=config.TRACK_B["cache_ttl_hours"])
        if cached is not None:
            return {date.fromisoformat(d): v for d, v in cached.items()}
        resp = cache.http_get(self.API, params={
            "action": "query", "prop": "revisions", "titles": title,
            "rvlimit": 500, "rvprop": "timestamp",
            "rvstart": "%sT23:59:59Z" % end.isoformat(),
            "rvend": "%sT00:00:00Z" % start.isoformat(),
            "format": "json", "formatversion": 2}, timeout=30,
            limiter=self._limiter)
        if resp is None or resp.status_code != 200:
            return None
        out: dict = {}
        for page in (resp.json().get("query") or {}).get("pages") or []:
            for rev in page.get("revisions") or []:
                try:
                    day = datetime.strptime(rev["timestamp"][:10],
                                            "%Y-%m-%d").date()
                except (KeyError, ValueError):
                    continue
                out[day] = out.get(day, 0.0) + 1.0
        cache.write(self.track, self.source, key,
                    {d.isoformat(): v for d, v in out.items()})
        return out

    def _from_api(self, sid: str) -> Optional[dict]:
        """Official JSON API. None when no key is set or the call fails."""
        key = os.environ.get("FRED_API_KEY")
        if not key:
            return None
        resp = cache.http_get(self.API, timeout=20, retries=2,
                              limiter=self._limiter,
                              params={"series_id": sid, "api_key": key,
                                      "file_type": "json"})
        if resp is None or resp.status_code != 200:
            return None
        try:
            rows = resp.json().get("observations", [])
        except Exception:
            return None
        out = {}
        for row in rows:
            value = str(row.get("value", "")).strip()
            if not value or value == ".":      # FRED marks gaps with a dot
                continue
            try:
                out[date.fromisoformat(row["date"])] = float(value)
            except (ValueError, KeyError):
                continue
        return out or None

    def _from_csv(self, sid: str) -> Optional[dict]:
        """Keyless fallback. Blocked from some cloud hosts — see API above."""
        resp = cache.http_get(self.CSV, params={"id": sid}, timeout=15,
                              limiter=self._limiter, retries=1)
        if resp is None or resp.status_code != 200:
            return None
        out = {}
        for line in resp.text.splitlines()[1:]:
            when, _, value = line.partition(",")
            value = value.strip()
            if not value or value == ".":
                continue
            try:
                out[date.fromisoformat(when.strip())] = float(value)
            except ValueError:
                continue
        return out or None


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", title)[:80]


class WikiPageviewsShareAdapter(WikiBase):
    """Candidate pageviews / sum of pageviews across the race's major
    candidates. The PROPORTIONAL form is better supported than raw volume.

    ATTENTION-ONLY. The scalar recorded is the CONCENTRATION of attention,
    |dem_share - rep_share| — how lopsided the race's attention is, with no
    direction attached. Recording dem_share itself would be a directional
    reading in all but name, and shares sum to 1 so their total carries no
    information at all.
    """

    name = "wiki_pageviews_share"

    def _reduce(self, series, start, end):
        totals = {}
        for party, per_day in series.items():
            vals = [v for day, (v, _) in per_day.items() if start <= day <= end]
            if not vals:
                return None
            totals[party] = sum(vals)
        grand = totals["dem"] + totals["rep"]
        if grand <= 0:
            return None
        shares = {p: totals[p] / grand for p in totals}
        return {
            "dem": {"value": shares["dem"]},
            "rep": {"value": shares["rep"]},
            "total": {"value": abs(shares["dem"] - shares["rep"])},
        }


class WikiEditCountAdapter(WikiBase):
    """Edits to the candidates' articles — ACTIVE engagement, as opposed to
    the passive attention pageviews measure. ATTENTION-ONLY."""

    name = "wiki_edit_count"

    def _reduce(self, series, start, end):
        sides = {}
        for party, per_day in series.items():
            sides[party] = {"value": float(sum(
                e for day, (_, e) in per_day.items() if start <= day <= end))}
        if not sides:
            return None
        return sides


# ===========================================================================
# Reddit — state subreddits
# ===========================================================================


def candidate_roster(race_id: str, meta: dict) -> Optional[dict]:
    """{"dem": ["Jon Ossoff"], "rep": [...]} for a race, or None.

    Taken from the FEC candidate search — a Track B source already wired up —
    with a hand-maintained override in config for governor races, which the
    FEC does not cover. It is NOT taken from the poll sheet: that is Track A
    data, and the two tracks share no inputs by construction.
    """
    override = config.TRACK_B.get("candidate_overrides", {}).get(race_id)
    if override:
        return {k: list(v) for k, v in override.items()}
    if meta["chamber"] == "governor":
        return None
    fec = FecBase()
    if not fec.is_available():
        return None
    out = {}
    for party, code in (("dem", "DEM"), ("rep", "REP")):
        names = [_humanize_fec_name(n)
                 for n in fec.candidate_names(race_id, meta, code)]
        names = [n for n in names if n]
        if not names:
            return None
        out[party] = names
    return out


# Honorifics the FEC carries inside the given-name field. They are titles,
# not names: leaving "MR." in produced the roster entry "David Alfred Mr.
# Pautsch", which no Wikipedia search can match. Generational suffixes
# (JR/SR/III) are deliberately NOT here — those are part of the legal name.
_FEC_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "hon", "rev", "prof",
                   "sen", "rep", "gov", "sgt", "capt", "col", "lt"}

# Kept upper-case verbatim by the title-casing pass below.
_ROMAN_SUFFIXES = {"ii", "iii", "iv", "v", "vi"}

def _humanize_fec_name(name: str) -> Optional[str]:
    """'OSSOFF, JON' -> 'Jon Ossoff'. Returns None on anything unparseable."""
    name = (name or "").strip()
    if not name:
        return None
    if "," in name:
        last, _, rest = name.partition(",")
        given = [p for p in rest.strip().split() if p]
        # The FEC puts a generational suffix in the GIVEN-name field:
        # "KEAN, THOMAS H JR" -> naive reassembly gives "Thomas H Jr Kean",
        # which is not a name anybody or any search index recognizes. Move it
        # back behind the surname where it belongs.
        suffix = []
        while given and given[-1].strip(".").lower() in _NAME_SUFFIXES:
            suffix.insert(0, given.pop())
        name = " ".join(given + [last.strip()] + suffix) if given \
            else " ".join([last.strip()] + suffix)
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    parts = [p for p in parts if p.strip(".").lower() not in _FEC_HONORIFICS]
    if not parts:
        return None
    # Roman-numeral suffixes must stay upper: .capitalize() turns III into
    # "Iii", which no Wikipedia title or search index matches.
    return " ".join(
        p if p.strip(".").lower() in _ROMAN_SUFFIXES
        else (p.capitalize() if p.isupper() else p)
        for p in parts)


# ===========================================================================
# Wikipedia — attention, and the first BACKTESTABLE Track B variable
# ===========================================================================

