"""
optimize_extended.py — re-optimize on the FULL 2.7yr dataset.

Objective = min(fold1, fold2, fold3) P&L — so a config is only rewarded if it's
profitable across ALL THREE regimes (2024, 2025, 2026), not just one.
"""
import pandas as pd
import optuna

import backtest
import config


def load_extended():
    df = pd.read_csv("data/XAUUSD_M5_202401020100_202609161245.csv", sep="\t")
    df["time"] = pd.to_datetime(df["<DATE>"] + " " + df["<TIME>"], format="%Y.%m.%d %H:%M:%S")
    df.set_index("time", inplace=True)
    df = df.rename(columns={"<OPEN>": "open", "<HIGH>": "high", "<LOW>": "low",
                            "<CLOSE>": "close", "<TICKVOL>": "tick_volume", "<SPREAD>": "spread"})
    return df[["open", "high", "low", "close", "tick_volume", "spread"]]


def main():
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    config.SESSION_START = "15:00"
    config.SESSION_END = "20:00"

    df = load_extended()
    meta = backtest.get_symbol_meta()
    n = len(df)
    third = n // 3
    p1, p2, p3 = df.iloc[:third], df.iloc[third:2 * third], df.iloc[2 * third:]
    recent = df[df.index >= df.index[-1] - pd.Timedelta(days=35)]
    print(f"{len(df)} bars | P1 {p1.index[0].date()}..{p1.index[-1].date()} | "
          f"P2 {p2.index[0].date()}..{p2.index[-1].date()} | P3 {p3.index[0].date()}..{p3.index[-1].date()}")

    def objective(trial):
        entry_mode = trial.suggest_categorical("entry_mode", ["kd_cross", "kd_state"])
        rearm_cd = trial.suggest_categorical("rearm_cooldown", [0, 3, 5])
        sl = trial.suggest_categorical("sl", [3, 5, 8, 10])
        partial_tp = trial.suggest_categorical("partial_tp", [0, 15, 20, 30, 40])
        max_bars = trial.suggest_categorical("max_bars", [10, 15, 20, 30])
        atr_low = trial.suggest_categorical("atr_low", [0.0, 0.5, 0.7])
        adx = trial.suggest_categorical("adx", [0.0, 25.0, 35.0, 45.0])
        rsi = trial.suggest_categorical("rsi_period", [7, 9, 14, 21, 28])
        stoch = trial.suggest_categorical("stoch_period", [5, 8, 11, 14])
        sk = trial.suggest_categorical("smooth_k", [2, 3, 5])
        sd = trial.suggest_categorical("smooth_d", [2, 3, 5])
        upper = trial.suggest_categorical("upper_limit", [75, 79, 80, 85])
        lower = trial.suggest_categorical("lower_limit", [15, 20, 23, 25])

        kw = dict(entry_mode=entry_mode, rearm_cooldown=rearm_cd, exit_mode="fade",
                  use_ma_filter=False, use_sl=True, sl_distance=sl, max_bars_held=max_bars,
                  direction="both", session_filter=True, meta=meta, partial_tp=partial_tp,
                  atr_low_factor=atr_low, atr_high_factor=0.0, adx_threshold=adx,
                  rsi_period=rsi, stoch_period=stoch, smooth_k=sk, smooth_d=sd,
                  lower_limit=lower, upper_limit=upper)

        _, _, r1 = backtest.run_backtest(p1, **kw)
        _, _, r2 = backtest.run_backtest(p2, **kw)
        _, _, r3 = backtest.run_backtest(p3, **kw)
        return min(r1["total_pnl"], r2["total_pnl"], r3["total_pnl"])

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=120, timeout=400)

    best = study.best_params
    kw = dict(exit_mode="fade", use_ma_filter=False, use_sl=True, direction="both",
              session_filter=True, meta=meta, atr_high_factor=0.0,
              entry_mode=best["entry_mode"], rearm_cooldown=best["rearm_cooldown"],
              sl_distance=best["sl"], partial_tp=best["partial_tp"],
              max_bars_held=best["max_bars"], atr_low_factor=best["atr_low"],
              adx_threshold=best["adx"], rsi_period=best["rsi_period"],
              stoch_period=best["stoch_period"], smooth_k=best["smooth_k"],
              smooth_d=best["smooth_d"], lower_limit=best["lower_limit"],
              upper_limit=best["upper_limit"])
    _, _, r1 = backtest.run_backtest(p1, **kw)
    _, _, r2 = backtest.run_backtest(p2, **kw)
    _, _, r3 = backtest.run_backtest(p3, **kw)
    _, _, rr = backtest.run_backtest(recent, **kw)

    print("\n=== BEST ROBUST PARAMS (min across 3 folds) ===")
    print(best)
    print(f"  P1: ${r1['total_pnl']:+.2f}  P2: ${r2['total_pnl']:+.2f}  P3: ${r3['total_pnl']:+.2f}  recent: ${rr['total_pnl']:+.2f}")

    best_obj = study.best_value
    print(f"\nBest objective (min fold P&L): ${best_obj:+.2f}")
    if best_obj > 0:
        print("=> A config profitable in ALL three regimes EXISTS.")
    else:
        print("=> NO config was profitable across all three regimes. The edge is regime-dependent.")


if __name__ == "__main__":
    main()
