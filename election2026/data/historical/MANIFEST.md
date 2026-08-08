# data/historical — backtest universe manifest

Fitting universe: competitive **Senate + House** races (governor rows kept
where present). Live pipeline scope is unchanged — battleground-only.

## Files
- `2016.csv` … `2024.csv` — Senate/governor rows, APPROXIMATE reconstructions;
  replace with measured values before serious fitting.

## Column provenance — READ THIS BEFORE TRUSTING A BACKTEST NUMBER

The files now mix two kinds of column, and they are NOT comparable:

| columns | provenance |
|---|---|
| `fec_z`, `trends_z`, `gdelt_z`, `youtube_z`, `adspend_z` | APPROXIMATE — hand-written placeholders, one decimal place |
| `econ_claims_z`, `econ_coincident_z`, `econ_unemployment_z` | **MEASURED** — computed 2026-08-02 by `python -m election2026 reconstruct-econ` from FRED, point-in-time at (election day − 14) |

Why this matters. The approximate columns correlate with `outcome_dem` at
+0.49 to +0.72, which is far higher than any real-world political signal
achieves on a battleground-only sample. They were written knowing the
outcomes. The measured economic columns correlate at +0.12 / +0.05 / −0.01,
which is what genuinely out-of-sample data looks like.

So a leave-one-variable-out comparison between the two groups does NOT rank
the variables — it ranks how much hindsight went into each column, and the
approximated ones win by construction. Do not retune λ or any weight from
that comparison. The fix is to replace the approximate columns with measured
ones, not to down-weight the measured variable for losing to a placeholder.
- `house_<cycle>_template.csv` — EMPTY House templates (2016/2018/2020/2022/
  2024). Fill by hand; loader ignores `*_template.csv` until renamed to e.g.
  `house_2018.csv`.

## Columns (all files)
race_id, cycle, chamber(senate|house|governor), p_consensus (P(Dem) ~2wk out,
0–1), outcome_dem (1=Dem won), fec_z, trends_z, gdelt_z, youtube_z, adspend_z
(Dem-oriented z-scores; blank = unavailable).

## What needs filling
Competitive House districts per cycle (prior work uses 60–100+/cycle:
Cook/Sabato Lean+Tossup universe): consensus-at-the-time, outcome, and any
reconstructible Track B z-scores. Sources are manual — do not script-fetch.
