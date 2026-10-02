# ADR-0012: GitHub Actions에서 Binance 공개 API 접근 불가 (451, 지역 차단)

**Status:** Accepted (research workaround only; live-trading question still open)
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

ADR-0009 implemented the funding-rate carry engine and assumed GitHub
Actions could reach Binance's public futures REST API the same way it
already reaches Upbit's public REST API (`swing_study.yml` has run real
Upbit studies successfully all session). That assumption was untested for
Binance specifically.

The first real-data dispatch of `funding_carry_study.yml` (H-0011,
BTCUSDT, run
[36400994154](https://github.com/tlsehd195/NEW2/actions/runs/36400994154))
failed immediately:

```
urllib.error.HTTPError: HTTP Error 451:
```

on `GET https://fapi.binance.com/fapi/v1/fundingRate`. HTTP 451
("Unavailable For Legal Reasons") is Binance's documented response when a
request originates from a restricted location. GitHub-hosted
`ubuntu-latest` runners run in US Azure datacenters, and Binance.com
(spot and futures) blocks US-origin IPs for regulatory reasons -- this
is a known, permanent, structural block (confirmed against multiple
independent public reports, e.g. ccxt issue trackers), not a transient
rate-limit or flake. It is very unlikely to resolve itself or to be
fixable by retrying, changing request headers, or picking a different
Binance REST path -- the block is applied at the network/IP layer before
the request reaches Binance's application logic.

This has two separate consequences worth separating clearly:

1. **Backtest data fetch** (this ADR's immediate trigger): funding-rate
   and futures-candle history for validation studies cannot be pulled
   through the current GitHub Actions runner.
2. **Live trading** (ADR-0002's actual goal -- Binance futures for
   leverage): if this block is truly IP/geography-based and not
   account-specific, *any* future automated process that calls Binance
   from a US-hosted server (this same GitHub Actions runner, most cheap
   US cloud VPS options) would hit the same wall for live order
   placement too, not just backtests. This project has not yet reached
   the live-trading wiring stage, so it has not been proven either way,
   but it is the more consequential open question and should be checked
   before ADR-0002's plan is relied on further.

Upbit is unaffected (Korean exchange, no evidence of blocking US-origin
IPs; `swing_study.yml` has run cleanly all session).

## Decision

**Live trading:** not yet decided -- still deferred to the account owner,
since every fix has a real cost or trust-boundary implication this
project's rules reserve for a person:

- **Self-hosted GitHub Actions runner** in a non-restricted region (e.g.
  a small always-on VPS in Korea/EU that the user provisions and
  registers as a runner) -- removes the block entirely, but is ongoing
  infrastructure the user has to pay for and keep alive, outside this
  session's reach.
- **A network proxy/relay service** for outbound Binance calls -- adds a
  third party in the request path for exchange data (and eventually
  order placement), which is a trust-boundary change or a paid rate,
  should be reviewed rather than adopted quietly.
- **Remote Control** -- run a session directly on the account owner's own
  device (Mac/Windows via Claude Desktop, or `claude remote-control` in a
  terminal). Since the owner is in Korea, their device is very unlikely
  to be geo-blocked. Needs no new server, but needs a computer available
  when work is done, and the repo cloned there.
- **Re-scope to Binance.US** -- does not offer futures/leverage to
  retail, so it would not satisfy ADR-0002's actual reason for choosing
  Binance in the first place; effectively abandons the leverage goal.

**Research/backtest data (accepted, implemented 2026-09-28):** use
`data.binance.vision` instead of `fapi.binance.com` for historical data
only. A connectivity check from a GitHub Actions job confirmed
`data.binance.vision` (a static-file CDN serving Binance's own published
monthly/daily archives, not an exchange API) returns HTTP 200 from the
same runner that gets 451 from `fapi.binance.com` on the same run. This
unblocks backtests without any new infrastructure and without the account
owner needing a computer available. It does **not** touch the live-trading
question above -- the archive only ever has already-published historical
data, so it is useless for placing real orders or reading the current
funding rate/price.

Implemented as `src/cointrader/data/binance_vision.py`
(`BinanceVisionFundingRateHistory`, `BinanceVisionFuturesCandles`,
`join_mark_price_from_candles`) and wired into
`scripts/run_funding_carry_study.py` as the new default
(`--data-source vision`, `--data-source live` keeps the old REST path for
wherever the block does not apply). The archive's funding-rate CSV has no
mark-price column (unlike the live REST endpoint), so `mark_price` is
filled explicitly from the nearest prior candle close
(`join_mark_price_from_candles`) -- a documented approximation of the
exchange's real mark-price index, not the live-quoted mark price itself;
this is called out in that module's docstring and is not hidden behind a
generic "binance_vision_archive" source string (the joined records carry
`source="binance_vision_archive+kline_close"`). Any archive file GitHub
does not have yet (a 404) is collected as a reported gap and printed, per
CLAUDE.md rule 4 -- never silently treated as "no funding event occurred".

No code path in `src/` should be changed to "work around" the live-trading
block by weakening fail-closed behavior (e.g. silently falling back to
stale or synthetic data) -- CLAUDE.md rule 4 applies here as much as
anywhere.

## Consequences

- The first H-0011 attempt could not be pre-registered against real data;
  no `research/preregistration.jsonl` entry or locked TEST window was
  created from it (the pre-registration step in the runner happened in
  the job's ephemeral checkout, never reached this repository, and is not
  reproduced retroactively). A retry using `--data-source vision`
  registers fresh, as normal.
- Live trading still needs an explicit answer from the account owner on
  which fix (if any) to pursue among the options above; until then,
  ADR-0002's Binance-futures live-trading plan stays unimplemented, not
  because the code isn't ready but because this environment cannot prove
  the exchange is reachable for it yet.
- Backtests are unblocked: `docs/PROJECT_STATUS.md` should be updated once
  H-0011 actually runs against archive data.
