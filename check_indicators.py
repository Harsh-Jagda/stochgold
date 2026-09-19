"""
check_indicators.py — print close / k / d for the most recent bars so you can
compare against MT5's Data Window (Ctrl+D) for your custom Stochastic RSI.

Usage:
    python check_indicators.py [count]     # count defaults to 10

Then in MT5: open XAUUSD M5, attach your custom Stochastic RSI (RSI 14, Stoch 8,
K 3, D 3, upper 79, lower 23), press Ctrl+D, hover the SAME bar time, and compare:
  * Close must match exactly (if not -> broker feed difference, not a formula bug)
  * %K vs 'k', %D vs 'd'
"""
import sys

import config
import indicators
import backtest


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    df = backtest.load_ohlc()
    k, d = indicators.compute_stoch_rsi(
        df["close"], config.RSI_PERIOD, config.STOCH_PERIOD, config.SMOOTH_K, config.SMOOTH_D,
    )
    out = df[["close"]].tail(count).copy()
    out["k"] = k.tail(count).round(4)
    out["d"] = d.tail(count).round(4)
    print(f"Symbol {config.SYMBOL} {config.TIMEFRAME} — last {count} bars (close/k/d):")
    print(out.to_string())


if __name__ == "__main__":
    main()
