"""
walkforward3.py — 3-fold walk-forward confirmation.

1) Evaluates the Optuna-found params on three consecutive folds.
2) Re-optimizes on fold 1 ONLY, then validates on folds 2 and 3 (both untouched),
   so we see whether the good params survive out-of-sample.
"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

import optuna

import backtest
import config


def load_fresh():
    mt5.initialize()
    rates = mt5.copy_rates_range(
        config.SYMBOL, config.mt5_timeframe(), datetime(2025, 3, 20), datetime(2026, 9, 2))
    mt5.shutdown()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df[["open", "high", "low", "close", "tick_volume", "spread"]]


def run_with(fold, meta, sl, partial_tp, atr_low, atr_high, max_bars):
    kw = dict(entry_mode="kd_cross", exit_mode="fade", use_ma_filter=False,
              use_sl=(sl > 0), sl_distance=sl, max_bars_held=max_bars,
              direction="both", session_filter=True, meta=meta,
              partial_tp=partial_tp, atr_low_factor=atr_low, atr_high_factor=atr_high)
    return backtest.run_backtest(fold, **kw)


def main():
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    config.SESSION_START = "15:00"
    config.SESSION_END = "20:00"

    df = load_fresh()
    meta = backtest.get_symbol_meta()
    n = len(df)
    third = n // 3
    p1, p2, p3 = df.iloc[:third], df.iloc[third:2 * third], df.iloc[2 * third:]

    print("=== 1) FOUND params (sl=5, partial_tp=30, atr_low=0.7, max_bars=20) on 3 folds ===")
    found = dict(sl=5, partial_tp=30, atr_low=0.7, atr_high=0.0, max_bars=20)
    for name, fold in [("P1", p1), ("P2", p2), ("P3", p3)]:
        _, _, r = run_with(fold, meta, **found)
        print(f"  {name} ({fold.index[0].date()}..{fold.index[-1].date()}): "
              f"${r['total_pnl']:>9.2f}  win {r['win_rate_pct']:.1f}%  {r['trades']} trades")

    print("\n=== 2) Re-optimize on P1 only, validate on P2 + P3 (untouched) ===")

    def objective(trial):
        sl = trial.suggest_categorical("sl", [0, 3, 5, 8, 10, 15])
        partial_tp = trial.suggest_categorical("partial_tp", [0, 10, 15, 20, 30, 40])
        atr_low = trial.suggest_categorical("atr_low", [0.0, 0.4, 0.5, 0.6, 0.7])
        atr_high = trial.suggest_categorical("atr_high", [0.0, 1.5, 2.0, 2.5])
        max_bars = trial.suggest_categorical("max_bars", [10, 15, 20, 25, 30])
        _, _, r = run_with(p1, meta, sl, partial_tp, atr_low, atr_high, max_bars)
        return r["total_pnl"]

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=100, timeout=240)

    best = study.best_params
    print(f"  best params: {best}")
    for name, fold in [("P1 (train)", p1), ("P2 (test)", p2), ("P3 (test)", p3)]:
        _, _, r = run_with(fold, meta, best["sl"], best["partial_tp"],
                           best["atr_low"], best["atr_high"], best["max_bars"])
        print(f"  {name:10}: ${r['total_pnl']:>9.2f}  win {r['win_rate_pct']:.1f}%  "
              f"{r['trades']} trades  maxDD ${r['max_drawdown']:.2f}")


if __name__ == "__main__":
    main()
