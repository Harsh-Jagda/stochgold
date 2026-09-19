"""
backtest.py - loads historical M5 OHLC, runs the exact indicators.py + strategy.py
functions that auto_bot.py uses, simulates fills honestly (spread + slippage +
broker-side stop loss), and reports win rate, total P&L, max drawdown, number of
trades and average bars held.

Usage:
    python backtest.py

It re-runs the strategy state machine bar-by-bar with strategy.evaluate_bar() - the
same function auto_bot.py calls - and additionally models the broker-side SL. When a
position is stopped out by SL, the state machine is reset exactly as it would be in
live trading, so stale signals are not applied to a position that no longer exists.
"""
import os
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

import config
import indicators
import strategy


def load_ohlc(start=None, end=None):
    """Load M5 OHLC for the configured range, preferring a cached CSV under data/."""
    start = start or config.BACKTEST_START
    end = end or config.BACKTEST_END

    os.makedirs("data", exist_ok=True)
    cache_path = os.path.join("data", f"{config.SYMBOL}_{config.TIMEFRAME}.csv")

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path, parse_dates=["time"])
        df.set_index("time", inplace=True)
        df = df[(df.index >= start_ts) & (df.index <= end_ts)]
        if len(df) > 2:
            if df.index.min() > start_ts:
                print(f"[note] cache starts at {df.index.min()} — broker has no data back to {start_ts}. "
                      f"Delete {cache_path} to force a re-pull after downloading more history.")
            print(f"Loaded {len(df)} bars from cache: {cache_path}")
            return df
        print(f"[warn] cache {cache_path} has only {len(df)} bar(s) — re-pulling from MT5.")
        os.remove(cache_path)

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

    try:
        rates = mt5.copy_rates_range(
            config.SYMBOL,
            config.mt5_timeframe(),
            start_ts.to_pydatetime(),
            end_ts.to_pydatetime(),
        )
    finally:
        mt5.shutdown()

    if rates is None or len(rates) == 0:
        print_history_diagnostic()
        raise RuntimeError(
            f"No data returned for {config.SYMBOL} {config.TIMEFRAME} in {start}..{end}. "
            f"Open an XAUUSD M5 chart in MT5 and scroll back to download history, then re-run."
        )

    if len(rates) < 2:
        print_history_diagnostic()
        raise RuntimeError(
            f"Only {len(rates)} bar(s) available in {start}..{end}. "
            f"See the diagnostic above — set BACKTEST_START/BACKTEST_END within the "
            f"available history range in config.py."
        )

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = df[["open", "high", "low", "close", "tick_volume", "spread"]]
    df.to_csv(cache_path)
    print(f"Pulled {len(df)} bars from MT5, cached to {cache_path}")
    return df


def print_history_diagnostic():
    """Report why history is missing: symbol name, Market Watch visibility, and range."""
    if not mt5.initialize():
        print("[diagnostic] Could not connect to MT5. Is the terminal running and logged in?")
        return
    try:
        info = mt5.symbol_info(config.SYMBOL)
        if info is None:
            print(f"[diagnostic] Symbol '{config.SYMBOL}' not found in the terminal.")
            candidates = []
            for pat in ("*XAU*", "*GOLD*"):
                res = mt5.symbols_get(pat)
                if res:
                    candidates += [s.name for s in res]
            if candidates:
                print(f"[diagnostic] Gold symbols available: {sorted(set(candidates))}")
                print("[diagnostic] Set SYMBOL in config.py to the correct name (brokers often add a suffix, e.g. XAUUSD.a).")
            else:
                print("[diagnostic] No gold symbols visible. In MT5 press Ctrl+U, find your gold symbol, and add it to Market Watch (Show Symbol).")
            return

        if not info.visible:
            print(f"[diagnostic] '{config.SYMBOL}' exists but is NOT in Market Watch. "
                  f"In MT5: Ctrl+U -> find it -> 'Show Symbol'.")

        rates = mt5.copy_rates_range(
            config.SYMBOL, config.mt5_timeframe(),
            datetime(2000, 1, 1), datetime(2030, 1, 1),
        )
        if rates is not None and len(rates) > 0:
            first = pd.to_datetime(rates[0]["time"], unit="s")
            last = pd.to_datetime(rates[-1]["time"], unit="s")
            print(f"[diagnostic] {config.SYMBOL} {config.TIMEFRAME} available: "
                  f"{len(rates)} bars from {first} to {last}")
            print("[diagnostic] Set BACKTEST_START/BACKTEST_END within that range.")
        else:
            print("[diagnostic] Symbol is correct but no M5 history downloaded yet.")
            print("[diagnostic] Open an XAUUSD M5 chart in MT5 and scroll back to force the download.")
            print("[diagnostic] Also check Tools -> Options -> Charts -> 'Max bars in chart' is high (e.g. 1000000 or Unlimited).")
    finally:
        mt5.shutdown()


def get_symbol_meta():
    """Return (point, trade_contract_size) from the broker, with XAUUSD-safe defaults."""
    point, contract = 0.01, 100
    if mt5.initialize():
        try:
            info = mt5.symbol_info(config.SYMBOL)
            if info is not None:
                point = info.point
                contract = info.trade_contract_size
        finally:
            mt5.shutdown()
    return point, contract


def run_backtest(df, sl_distance=None, use_ma_filter=None, ma_period=None,
                 session_filter=None, meta=None, exit_mode="cross_dir",
                 entry_mode="d_limit", k_confirm=False, use_sl=True,
                 max_bars_held=None, direction="both", depth_extra=0.0,
                 trail_stop=0.0, trail_activate=0.0, tp=0.0, partial_tp=0.0,
                 atr_period=14, atr_low_factor=0.0, atr_high_factor=0.0,
                 adx_threshold=0.0,
                 rsi_period=None, stoch_period=None, smooth_k=None, smooth_d=None,
                 lower_limit=None, upper_limit=None, rearm_cooldown=0,
                 sr_lookback=0, sr_zone=0.25, exit_fill="next_open"):
    df = df.copy()
    if rsi_period is None:
        rsi_period = config.RSI_PERIOD
    if stoch_period is None:
        stoch_period = config.STOCH_PERIOD
    if smooth_k is None:
        smooth_k = config.SMOOTH_K
    if smooth_d is None:
        smooth_d = config.SMOOTH_D
    if lower_limit is None:
        lower_limit = config.LOWER_LIMIT
    if upper_limit is None:
        upper_limit = config.UPPER_LIMIT
    df["k"], df["d"] = indicators.compute_stoch_rsi(
        df["close"], rsi_period, stoch_period, smooth_k, smooth_d,
    )
    if ma_period is None:
        ma_period = config.MA_PERIOD
    df["ma"] = indicators.compute_ma(df["close"], ma_period, config.MA_METHOD)

    if sl_distance is None:
        sl_distance = config.STOP_LOSS_DISTANCE_USD
    if use_ma_filter is None:
        use_ma_filter = config.USE_MA_FILTER
    if session_filter is None:
        session_filter = config.SESSION_FILTER_BACKTEST

    if meta is None:
        meta = get_symbol_meta()
    point, contract = meta
    if max_bars_held is None:
        max_bars_held = config.MAX_BARS_HELD
    lots = config.BACKTEST_LOT_SIZE
    slip_pts = config.BACKTEST_SLIPPAGE_POINTS
    spread_override = config.BACKTEST_SPREAD_POINTS

    # Session window (Dubai time). Bar timestamps are assumed to be MT5 server time;
    # SERVER_UTC_OFFSET_HOURS corrects that if the server is not UTC.
    start_t = datetime.strptime(config.SESSION_START, "%H:%M").time()
    end_t = datetime.strptime(config.SESSION_END, "%H:%M").time()
    offset_min = (config.SESSION_UTC_OFFSET_HOURS - config.SERVER_UTC_OFFSET_HOURS) * 60

    k = df["k"].values
    d = df["d"].values
    close = df["close"].values
    open_ = df["open"].values
    low = df["low"].values
    high = df["high"].values
    spread = df["spread"].values
    ma = df["ma"].values
    times = df.index

    if session_filter:
        start_min = start_t.hour * 60 + start_t.minute
        end_min = end_t.hour * 60 + end_t.minute
        buffer_min = end_min - config.NO_NEW_TRADES_BUFFER_MIN
        minutes = times.hour.to_numpy() * 60 + times.minute.to_numpy() + offset_min
        in_session_arr = (minutes >= start_min) & (minutes < end_min)
        in_buffer_arr = (minutes >= buffer_min) & (minutes < end_min)
        session_end_arr = minutes >= end_min

    if atr_low_factor > 0 or atr_high_factor > 0:
        atr = indicators.compute_atr(df, atr_period)
        atr_med = atr.rolling(100, min_periods=1).median()
        atr_arr = atr.values
        atr_med_arr = atr_med.values
    else:
        atr_arr = None
        atr_med_arr = None

    if adx_threshold > 0:
        adx_arr = indicators.compute_adx(df, 14).values
    else:
        adx_arr = None

    if sr_lookback > 0:
        roll_low = df["low"].rolling(sr_lookback).min()
        roll_high = df["high"].rolling(sr_lookback).max()
        roll_range = (roll_high - roll_low).replace(0, np.nan)
        sr_pos = ((df["close"] - roll_low) / roll_range).values
    else:
        sr_pos = None

    trades = []
    balance = 0.0
    equity_curve = []
    open_trade = None
    state = strategy.initial_state()
    cooldown = 0

    def close_position(exit_price, reason, ts):
        nonlocal open_trade, balance
        rem = open_trade.get("remaining_lots", lots)
        if open_trade["side"] == "LONG":
            pnl = (exit_price - open_trade["entry"]) * contract * rem
        else:
            pnl = (open_trade["entry"] - exit_price) * contract * rem
        balance += pnl
        trades.append({
            "side": open_trade["side"],
            "entry_time": open_trade["entry_time"],
            "exit_time": ts,
            "entry": round(open_trade["entry"], 3),
            "exit": round(exit_price, 3),
            "bars_held": open_trade["bars"],
            "pnl": round(pnl, 2),
            "reason": reason,
        })
        open_trade = None

    def new_trade(side, entry, ts):
        return {"side": side, "entry": entry,
                "sl": entry - sl_distance if side == "LONG" else entry + sl_distance,
                "bars": 0, "entry_time": ts,
                "best": entry, "armed": trail_activate <= 0,
                "remaining_lots": lots, "partial_done": False}

    for i in range(1, len(df)):
        if pd.isna(k[i]) or pd.isna(d[i]) or pd.isna(k[i - 1]) or pd.isna(d[i - 1]):
            equity_curve.append(balance)
            continue

        if cooldown > 0:
            cooldown -= 1

        if session_filter:
            in_session = bool(in_session_arr[i])
            in_buffer = bool(in_buffer_arr[i])
            is_session_end = bool(session_end_arr[i])
        else:
            in_session, in_buffer, is_session_end = True, False, False

        state_before = dict(state)
        sig = strategy.evaluate_bar(k[i - 1], d[i - 1], k[i], d[i], state,
                                    use_ma_filter=use_ma_filter,
                                    close_curr=close[i], ma_curr=ma[i],
                                    entry_mode=entry_mode, exit_mode=exit_mode,
                                    k_confirm=k_confirm, max_bars_held=max_bars_held,
                                    direction=direction, depth_extra=depth_extra,
                                    lower_limit=lower_limit, upper_limit=upper_limit)

        # Gate NEW entries: session window + ATR volatility filter (exits always allowed).
        if sig in ("BUY", "SELL") and open_trade is None:
            blocked = (session_filter and (not in_session or in_buffer))
            if not blocked and atr_arr is not None and not pd.isna(atr_arr[i]):
                a = atr_arr[i]
                m = atr_med_arr[i]
                if atr_low_factor > 0 and a < m * atr_low_factor:
                    blocked = True
                if atr_high_factor > 0 and a > m * atr_high_factor:
                    blocked = True
            if not blocked and adx_arr is not None and not pd.isna(adx_arr[i]):
                if adx_threshold > 0 and adx_arr[i] > adx_threshold:
                    blocked = True
            if not blocked and cooldown > 0:
                blocked = True
            # support/resistance: longs only near support, shorts only near resistance
            if not blocked and sr_pos is not None and not pd.isna(sr_pos[i]):
                if sig == "BUY" and sr_pos[i] > sr_zone:
                    blocked = True
                elif sig == "SELL" and sr_pos[i] < (1 - sr_zone):
                    blocked = True
            if blocked:
                state.update(state_before)  # revert entry consumption
                sig = None

        spread_pts = spread_override if spread_override is not None else int(spread[i] or 0)
        half_cost = ((spread_pts / 2) + slip_pts) * point
        ts = times[i]
        external_close = False

        # 1) broker-side SL check on an existing position (only if SL is used)
        if open_trade is not None:
            open_trade["bars"] += 1
            if use_sl:
                if open_trade["side"] == "LONG" and low[i] <= open_trade["sl"]:
                    close_position(open_trade["sl"] - slip_pts * point, "sl", ts)
                    external_close = True
                    cooldown = rearm_cooldown
                elif open_trade["side"] == "SHORT" and high[i] >= open_trade["sl"]:
                    close_position(open_trade["sl"] + slip_pts * point, "sl", ts)
                    external_close = True
                    cooldown = rearm_cooldown

            # 1b) trailing stop (arms after trail_activate profit, trails from the peak)
            if not external_close and trail_stop > 0:
                if open_trade["side"] == "LONG":
                    open_trade["best"] = max(open_trade["best"], high[i])
                    if not open_trade["armed"] and (open_trade["best"] - open_trade["entry"]) >= trail_activate:
                        open_trade["armed"] = True
                    if open_trade["armed"] and low[i] <= open_trade["best"] - trail_stop:
                        close_position(open_trade["best"] - trail_stop - slip_pts * point, "trail", ts)
                        external_close = True
                else:
                    open_trade["best"] = min(open_trade["best"], low[i])
                    if not open_trade["armed"] and (open_trade["entry"] - open_trade["best"]) >= trail_activate:
                        open_trade["armed"] = True
                    if open_trade["armed"] and high[i] >= open_trade["best"] + trail_stop:
                        close_position(open_trade["best"] + trail_stop + slip_pts * point, "trail", ts)
                        external_close = True

            # 1c) take profit
            if not external_close and tp > 0:
                if open_trade["side"] == "LONG" and high[i] >= open_trade["entry"] + tp:
                    close_position(open_trade["entry"] + tp, "tp", ts)
                    external_close = True
                elif open_trade["side"] == "SHORT" and low[i] <= open_trade["entry"] - tp:
                    close_position(open_trade["entry"] - tp, "tp", ts)
                    external_close = True

            # 1d) partial take profit: book half at partial_tp, let the rest run
            if not external_close and partial_tp > 0 and not open_trade.get("partial_done"):
                hit = (open_trade["side"] == "LONG" and high[i] >= open_trade["entry"] + partial_tp) or \
                      (open_trade["side"] == "SHORT" and low[i] <= open_trade["entry"] - partial_tp)
                if hit:
                    half = open_trade["remaining_lots"] / 2.0
                    exit_p = (open_trade["entry"] + partial_tp) if open_trade["side"] == "LONG" \
                        else (open_trade["entry"] - partial_tp)
                    if open_trade["side"] == "LONG":
                        pnl = (exit_p - open_trade["entry"]) * contract * half
                    else:
                        pnl = (open_trade["entry"] - exit_p) * contract * half
                    balance += pnl
                    trades.append({
                        "side": open_trade["side"],
                        "entry_time": open_trade["entry_time"],
                        "exit_time": ts,
                        "entry": round(open_trade["entry"], 3),
                        "exit": round(exit_p, 3),
                        "bars_held": open_trade["bars"],
                        "pnl": round(pnl, 2),
                        "reason": "partial_tp",
                    })
                    open_trade["remaining_lots"] = half
                    open_trade["partial_done"] = True

        # 2) session-end force-flatten (unconditional)
        if session_filter and open_trade is not None and is_session_end:
            exit_price = (close[i] - half_cost if open_trade["side"] == "LONG"
                          else close[i] + half_cost)
            close_position(exit_price, "session_end", ts)
            external_close = True

        # 3) normal signal handling (entries and signal exits)
        # Honest fills: the signal is only known at bar i's CLOSE, so the trade
        # can't fill at that close. It fills at the NEXT bar's open. This removes
        # look-ahead bias and matches what the live bot actually does.
        if not external_close:
            nxt = open_[i + 1] if i + 1 < len(df) else close[i]
            if open_trade is None:
                if sig == "BUY":
                    entry = nxt + half_cost
                    open_trade = new_trade("LONG", entry, ts)
                elif sig == "SELL":
                    entry = nxt - half_cost
                    open_trade = new_trade("SHORT", entry, ts)
            else:
                if sig == "CLOSE_LONG" and open_trade["side"] == "LONG":
                    if exit_fill == "intrabar" and d[i - 1] != d[i]:
                        frac = (d[i - 1] - upper_limit) / (d[i - 1] - d[i])
                        exit_p = open_[i] + frac * (close[i] - open_[i])
                        close_position(exit_p - half_cost, "signal", ts)
                    else:
                        close_position(nxt - half_cost, "signal", ts)
                elif sig == "CLOSE_SHORT" and open_trade["side"] == "SHORT":
                    if exit_fill == "intrabar" and d[i] != d[i - 1]:
                        frac = (lower_limit - d[i - 1]) / (d[i] - d[i - 1])
                        exit_p = open_[i] + frac * (close[i] - open_[i])
                        close_position(exit_p + half_cost, "signal", ts)
                    else:
                        close_position(nxt + half_cost, "signal", ts)
                elif sig == "SELL" and open_trade["side"] == "LONG":
                    # reversal: close the long and open a short on the same signal
                    close_position(nxt - half_cost, "reverse", ts)
                    entry = nxt - half_cost
                    open_trade = new_trade("SHORT", entry, ts)
                elif sig == "BUY" and open_trade["side"] == "SHORT":
                    # reversal: close the short and open a long on the same signal
                    close_position(nxt + half_cost, "reverse", ts)
                    entry = nxt + half_cost
                    open_trade = new_trade("LONG", entry, ts)
        else:
            # Position was closed outside the state machine (SL / session end).
            state = strategy.initial_state()

        # Mark-to-market equity for drawdown tracking.
        if open_trade is None:
            equity_curve.append(balance)
        elif open_trade["side"] == "LONG":
            equity_curve.append(balance + (close[i] - open_trade["entry"]) * contract * lots)
        else:
            equity_curve.append(balance + (open_trade["entry"] - close[i]) * contract * lots)

    # Force-close any position still open at the end of the data.
    if open_trade is not None:
        close_position(close[-1], "end", times[-1])

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    avg_bars = float(np.mean([t["bars_held"] for t in trades])) if n else 0.0

    equity_arr = np.array(equity_curve) if equity_curve else np.array([0.0])
    running_peak = np.maximum.accumulate(equity_arr)
    max_dd = float((running_peak - equity_arr).max())

    report = {
        "bars": len(df),
        "trades": n,
        "win_rate_pct": round(wins / n * 100, 2) if n else 0.0,
        "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_dd, 2),
        "avg_bars_held": round(avg_bars, 2),
    }
    return df, trades, report


def daily_summary(trades):
    """Bucket trade P&L by exit date and print a day-by-day view."""
    if not trades:
        print("  (no trades)")
        return
    t = pd.DataFrame(trades)
    t["exit_time"] = pd.to_datetime(t["exit_time"])
    daily = t.groupby(t["exit_time"].dt.date)["pnl"].sum()
    green = daily[daily > 0]
    red = daily[daily < 0]
    print(f"  Days traded   : {len(daily)}")
    print(f"  Green days    : {(daily > 0).mean()*100:.0f}%")
    print(f"  Avg day P&L   : ${daily.mean():+.2f}")
    print(f"  Best day      : ${daily.max():+.2f}")
    print(f"  Worst day     : ${daily.min():+.2f}")
    if len(green):
        print(f"  Avg green day : ${green.mean():+.2f}")
    if len(red):
        print(f"  Avg red day   : ${red.mean():+.2f}")


def main():
    print(f"=== Backtest {config.SYMBOL} {config.TIMEFRAME} ({config.BACKTEST_START}..{config.BACKTEST_END}) ===")
    print(f"NOTE: SL=${config.STOP_LOSS_DISTANCE_USD:.2f} | MA filter={'ON' if config.USE_MA_FILTER else 'OFF'} "
          f"| session={'ON ' + config.SESSION_START + '-' + config.SESSION_END if config.SESSION_FILTER_BACKTEST else 'OFF'} "
          f"| risk ${config.RISK_USD:.2f}/trade at {config.BACKTEST_LOT_SIZE} lot.\n")

    df = load_ohlc()
    _, trades, report = run_backtest(df)

    print(f"\nBars processed : {report['bars']}")
    print(f"Trades         : {report['trades']}")
    print(f"Win rate       : {report['win_rate_pct']}%")
    print(f"Total P&L      : ${report['total_pnl']:,.2f}")
    print(f"Max drawdown   : ${report['max_drawdown']:,.2f}")
    print(f"Avg bars held  : {report['avg_bars_held']}")

    if trades:
        t = pd.DataFrame(trades)
        t.to_csv("data/backtest_trades.csv", index=False)
        print(f"\nTrade list saved to data/backtest_trades.csv ({len(t)} trades)")


if __name__ == "__main__":
    main()
