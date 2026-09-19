"""Generate the equity curve PNG for the LinkedIn post (clean, no em dashes)."""
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch

import backtest
import config

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

_, trades, _ = backtest.run_backtest(df, **kw)

t = pd.DataFrame(trades)
t["exit_time"] = pd.to_datetime(t["exit_time"])
t = t.sort_values("exit_time")
t["cum"] = t["pnl"].cumsum()

n = len(df)
b1 = df.index[n // 3]
b2 = df.index[2 * n // 3]

fig, ax = plt.subplots(figsize=(11, 5.5), dpi=160)
ax.plot(t["exit_time"], t["cum"], color="#1a7f4b", lw=1.6, zorder=3)

ax.axvspan(df.index[0], b1, color="#c0392b", alpha=0.10)
ax.axvspan(b1, b2, color="#f0b429", alpha=0.10)
ax.axvspan(b2, df.index[-1], color="#1a7f4b", alpha=0.10)

ax.axvline(b1, color="#888", ls="--", lw=0.8)
ax.axvline(b2, color="#888", ls="--", lw=0.8)

ax.set_title("Gold M5 mean-reversion bot: cumulative P&L (demo)", fontsize=13, weight="bold")
ax.set_ylabel("Cumulative P&L ($)", fontsize=11)
ax.grid(True, alpha=0.25)

legend = [
    Patch(facecolor="#c0392b", alpha=0.14, label="2024 (no edge)"),
    Patch(facecolor="#f0b429", alpha=0.14, label="transition"),
    Patch(facecolor="#1a7f4b", alpha=0.14, label="2025-26 (regime edge)"),
]
ax.legend(handles=legend, loc="upper left", fontsize=9, framealpha=0.9)

ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
fig.autofmt_xdate()
fig.tight_layout()
fig.savefig("equity_curve.png", bbox_inches="tight")
print("saved equity_curve.png")
