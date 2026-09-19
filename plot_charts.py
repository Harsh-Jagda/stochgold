"""Generate the comparison bar chart and K/D indicator chart (clean, no em dashes)."""
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import backtest
import config
import indicators

df = pd.read_csv("data/XAUUSD_M5_202401020100_202609161245.csv", sep="\t")
df["time"] = pd.to_datetime(df["<DATE>"] + " " + df["<TIME>"], format="%Y.%m.%d %H:%M:%S")
df.set_index("time", inplace=True)
df = df.rename(columns={"<OPEN>": "open", "<HIGH>": "high", "<LOW>": "low",
                        "<CLOSE>": "close", "<TICKVOL>": "tick_volume", "<SPREAD>": "spread"})
df = df[["open", "high", "low", "close", "tick_volume", "spread"]]

config.SESSION_START = "15:00"
config.SESSION_END = "20:00"
meta = backtest.get_symbol_meta()

kw = dict(entry_mode="kd_cross", rearm_cooldown=5, exit_mode="fade", use_ma_filter=False,
          use_sl=True, sl_distance=10, max_bars_held=20, direction="both",
          session_filter=True, meta=meta, partial_tp=40, atr_low_factor=0.5,
          atr_high_factor=0.0, adx_threshold=35, rsi_period=21, stoch_period=8,
          smooth_k=3, smooth_d=3, lower_limit=23, upper_limit=80)

n = len(df)
third = n // 3
folds = [df.iloc[:third], df.iloc[third:2 * third], df.iloc[2 * third:]]
fold_labels = ["2024\n(Jan-Nov)", "2025\n(Nov-Oct)", "2026\n(Oct-Sep)"]
fold_pnls = []
for f in folds:
    _, _, r = backtest.run_backtest(f, **kw)
    fold_pnls.append(r["total_pnl"])

# ---- bar chart ----
fig, ax = plt.subplots(figsize=(7, 4.5), dpi=160)
colors = ["#c0392b" if v < 0 else "#1a7f4b" for v in fold_pnls]
bars = ax.bar(fold_labels, fold_pnls, color=colors, width=0.55)
ax.axhline(0, color="#333", lw=1)
ax.set_ylabel("P&L ($)", fontsize=11)
ax.set_title("The full picture, per regime", fontsize=13, weight="bold")
for b, v in zip(bars, fold_pnls):
    off = 14 if v > 0 else -38
    ax.text(b.get_x() + b.get_width() / 2, v + off, f"${v:+.0f}",
            ha="center", fontsize=11, weight="bold")
ax.set_ylim(min(fold_pnls) - 75, max(fold_pnls) + 45)
ax.text(0.5, -0.22, "The original 17-month backtest only covered the green bars.",
        transform=ax.transAxes, ha="center", va="top", fontsize=9, color="#666", style="italic")
fig.tight_layout()
fig.savefig("comparison_17mo_vs_2yr.png", bbox_inches="tight")

# ---- K/D indicator ----
window = df.iloc[-600:]
k, d = indicators.compute_stoch_rsi(window["close"], 21, 8, 3, 3)

fig, ax = plt.subplots(figsize=(10, 4), dpi=160)
ax.plot(window.index, k, color="#1a7f4b", lw=1.3, label="%K")
ax.plot(window.index, d, color="#c0392b", lw=1.3, label="%D")
ax.axhline(80, color="#c0392b", ls="--", lw=0.8, alpha=0.55)
ax.axhline(23, color="#1a7f4b", ls="--", lw=0.8, alpha=0.55)
ax.fill_between(window.index, 80, 100, color="#c0392b", alpha=0.07)
ax.fill_between(window.index, 0, 23, color="#1a7f4b", alpha=0.07)
ax.set_ylim(0, 100)
ax.set_ylabel("Stochastic RSI", fontsize=11)
ax.set_title("The signal: %K crosses %D in oversold / overbought", fontsize=13, weight="bold")
ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
ax.grid(True, alpha=0.25)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d %H:%M"))
fig.autofmt_xdate()
fig.tight_layout()
fig.savefig("kd_indicator.png", bbox_inches="tight")

print("saved comparison_17mo_vs_2yr.png and kd_indicator.png")
