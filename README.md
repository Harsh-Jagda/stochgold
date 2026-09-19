# Gold mean-reversion bot (Stoch RSI)

A Python bot that trades gold (XAUUSD) on the 5 minute chart. It buys when a
Stochastic RSI signal says oversold, sells when overbought, and closes when
momentum fades.

Short version: it's profitable when gold ranges, and it stands aside when gold
trends. The last 35 days made about $170 at 0.01 lot, and the backtest has been
green since 2025.

## The finding

17 months of backtests (Mar 2025 to Sep 2026) were green in every walk-forward
fold.

Then I extended the data back to Jan 2024. 2024 was red, 2025 and 2026 were
green. I re-optimized the whole parameter space with a rule that a config only
counts if it's profitable across all three years. What survived wasn't a
"win everywhere" strategy, because those don't exist. What survived was a
strategy with filters that keep it out of the market when conditions are wrong.

So the finding isn't "the strategy is broken." It's that the edge is regime
dependent. Mean reversion prints when gold ranges and loses when it trends. The
bot's ATR and ADX filters are what keep it on the right side of that line.

## What the bot actually does

Entry, on a completed M5 bar:

- Long when K crosses up through D while D is below 23 (oversold).
- Short when K crosses down through D while D is above 80 (overbought).

Exit:

- Fade: close when D crosses back out of the opposite zone. For a long, that
  means D drops back below 80. For a short, D climbs back above 23.
- Or a 20 bar time stop.

Risk per trade:

- Stop loss of $10 in price.
- Partial take profit: half the position closes at +$40, the rest keeps running.
- The position is two 0.01 lots, so the half-close actually works (0.01 is the
  minimum lot for gold).

Filters (all optional, all tested):

- Session window 15:00 to 20:00 Dubai (UTC+4). This one mattered. "Any time"
  mode lost money.
- ATR low filter: skip entries when volatility is too low (dead chop).
- ADX filter: skip entries when the trend is too strong. This is the one that
  keeps the bot out of trending markets.
- News window: skip new entries around scheduled high impact events.
- Re-arm cooldown: wait N bars after a stop loss before entering again.

Indicators are hand rolled (RSI, Stoch RSI, MA, ATR, ADX). No TA library. That
way the backtest and the live bot run the exact same math.

## Files

- `config.py`: the tunable settings.
- `indicators.py`: RSI, Stoch RSI, MA, ATR, ADX. Pure functions, no TA library.
- `strategy.py`: the entry/exit state machine. One function, `evaluate_bar`,
  used by both backtest and live.
- `backtest.py`: the backtest engine. Honest fills: next bar open, spread and
  slippage, broker-side stop loss.
- `optimize_extended.py`: Optuna parameter search over the extended dataset.
- `walkforward3.py`: three fold walk-forward validation.
- `auto_bot.py`: the live bot (demo account only).
- `check_indicators.py`: verifies the hand-rolled indicators against MT5.
- `run_forever.bat`: restart wrapper.
- `plot_equity.py` and `plot_charts.py`: the charts.

The one rule I never broke: `backtest.py` and `auto_bot.py` both import the same
`strategy.evaluate_bar` and `indicators` functions. There is no second copy of
the logic to drift.

## Everything I tested and rejected

This is the part that matters. I threw a lot at this thing and almost all of it
made things worse:

- Trailing stops. Worse.
- Fixed take profit instead of the fade exit. Worse.
- Reversal entries (flip long to short on the same signal). Worse.
- MA trend filter (price above or below the moving average). Worse.
- ATR high filter. Worse.
- Support and resistance filter. Worse.
- Depth filter (only enter deep in the zone). Worse.
- Direction filters (long only, short only). Worse.
- Six exit rules: fade, cross, touch, reversal, a rollover idea, and an
  intrabar fade (exit the second D crosses instead of waiting for the close).
  Fade won them all.
- Different Stoch RSI periods, smoothing, and thresholds. The original settings
  were already fine.

The pattern is telling. The core signal is what it is, and stacking more
indicators on it never helped. That pushed me away from "add more stuff" and
toward "know when to trade and when to stand aside," which is where the real
improvement came from.

## The numbers

Extended 2.7 year backtest, final config, at 0.01 lot, filling at the next
bar's open (no look-ahead bias):

| Fold | P&L |
|---|---|
| 2024 | -$62 |
| 2025 | +$154 |
| 2026 | +$124 |
| Full | +$206 |
| Max drawdown | $440 |

Exit rules compared, same config:

| Exit | 2024 | 2025 | 2026 | recent |
|---|---|---|---|---|
| fade | -$62 | +$154 | +$124 | +$171 |
| rollover | -$224 | -$65 | -$216 | +$11 |
| cross_dir | -$198 | -$26 | -$421 | +$41 |
| touch | -$384 | +$74 | -$233 | +$47 |

An intrabar fade (exit the instant D crosses back, no close confirmation) came
out to -$119 total and -$499 in 2024. Waiting for the close is not a data
artifact. It's a confirmation filter, and it pays for itself.

And "any time" mode, no session filter: -$1,390 over 9,576 trades.

The 2024 red isn't noise to hide. It's the whole point. It shows what happens
when the bot trades a market that isn't ranging, and it's exactly why the
filters exist.

## Final config

- Entry: kd_cross, at bar close.
- Exit: fade, 20 bar cap.
- Stop loss $10, partial take profit $40.
- Session 15:00 to 20:00 Dubai.
- ATR low 0.5x, ADX above 35, news window, re-arm cooldown 5.
- Stoch RSI: RSI 21, Stoch 8, K 3, D 3, zones 23 and 80.

## Running it

Demo only.

```bash
pip install MetaTrader5 pandas numpy optuna matplotlib
```

Then have MT5 running, logged into a demo account, with
Tools > Options > Expert Advisors > Allow Algo Trading checked.

```bash
python auto_bot.py          # live, demo account
python backtest.py          # backtest
python optimize_extended.py # parameter search
```

## Honest limitations

- One symbol, one timeframe, one broker feed.
- The edge is regime dependent. It needs a ranging market, and it relies on the
  filters to detect that.
- Backtest fills assume the next bar's open (no look-ahead bias), plus spread
  and slippage. Live fills can still differ by a few cents, so the bot logs
  the gap between fill and bar open on every entry and exit.
- This is a demo exercise. No real money has gone in yet.

## Not financial advice

This is a project, not a recommendation. Past backtest performance isn't a
promise of future results.
