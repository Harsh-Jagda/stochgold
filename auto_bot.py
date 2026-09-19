"""
auto_bot.py — AUTO-TRADING bot for the validated manual method (DEMO ONLY).

Takes the exact same signals as the signal assistant and opens/closes trades:
  * ENTRY : K crosses D in oversold (<23) -> BUY  /  overbought (>79) -> SELL,
            with a generous take-profit attached.
  * EXIT  : fade (D crosses back out of the opposite extreme) or 15-bar time stop.

Risk knobs (top of file): LOT_SIZE, HALF_LOT, PARTIAL_TP_USD, SL_USD.
Runs any time the market is open (ENFORCE_SESSION=False). DEMO — verify before live.
"""
import csv
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

import config
import indicators
import strategy

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TZ = timezone(timedelta(hours=config.SESSION_UTC_OFFSET_HOURS))

ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

_heartbeat = {"t": time.time()}


def _watchdog(timeout_sec=600):
    """Force-exit if the main loop stops making progress (e.g. a blocked MT5 call),
    so the run_forever.bat wrapper can restart us."""
    while True:
        time.sleep(30)
        if time.time() - _heartbeat["t"] > timeout_sec:
            try:
                sys.stdout.write(f"[watchdog] bot stalled {timeout_sec}s — forcing restart\n")
                sys.stdout.flush()
            except Exception:
                pass
            os._exit(1)


class ConsoleTee:
    """Tees stdout/stderr to a daily log file (ANSI colors stripped, timestamps added)."""
    def __init__(self, filepath):
        self.file = open(filepath, "a", encoding="utf-8")
        self.stdout = sys.__stdout__
        self.at_line_start = True

    def write(self, text):
        self.stdout.write(text)
        self.stdout.flush()
        clean = ANSI_RE.sub("", text)
        for line in clean.splitlines(True):
            if self.at_line_start:
                ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                self.file.write(f"{ts} | ")
            self.file.write(line)
            self.at_line_start = line.endswith("\n")
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

# --- Validated method ---
ENTRY_MODE = "kd_cross"
EXIT_MODE = "fade"
USE_MA_FILTER = False
MAX_BARS = 20
DIRECTION = "both"
ENFORCE_SESSION = True

# --- Risk / execution knobs ---
LOT_SIZE = 0.02                      # total exposure (split into two 0.01 halves)
HALF_LOT = 0.01
PARTIAL_TP_USD = 40.0                # half the position takes profit at this level (extended backtest)
SL_USD = 10.0                        # stop loss on the full position (extended backtest)
POLL_SEC = 10                        # how often to poll the forming bar while flat
REARM_COOLDOWN = 5                   # bars to wait after a stop-loss before re-entering
ATR_LOW_FACTOR = 0.5                 # skip entries when ATR < 0.5x its 100-bar median (dead chop); 0 = off
ATR_HIGH_FACTOR = 0.0                 # high-vol filter OFF (Optuna found it hurt consistency)
ADX_THRESHOLD = 35.0                  # skip entries when ADX > this (strong trend); 0 = off
NEWS_BUFFER_MIN = 30                  # skip new entries ± this many minutes around high-impact events
NEWS_SPECIFIC_DUBAI = []              # one-off events, e.g. ["2026-09-16 16:30"] (Dubai time)

STATE_FILE = "bot_state.json"
LOG_FILE = "trade_log.csv"
LOG_COLUMNS = ["timestamp", "bar_time", "event", "side", "k", "d", "price", "lots", "ticket", "bar_open", "fill_gap", "note"]

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def connect_with_retry(max_attempts=5, delay_sec=5):
    for attempt in range(1, max_attempts + 1):
        if mt5.initialize():
            print(f"[connect] MT5 initialized (attempt {attempt})")
            return
        print(f"[connect] attempt {attempt}/{max_attempts} failed — retrying in {delay_sec}s")
        time.sleep(delay_sec)
    raise RuntimeError(f"MT5 connection failed: {mt5.last_error()}")


def now_dubai():
    return datetime.now(TZ)


def parse_hhmm(s):
    return datetime.strptime(s, "%H:%M").time()


def bar_dubai_str(ts):
    # MT5 returns bar times in broker SERVER time (UTC+3), not UTC.
    # Dubai is UTC+4, so shift by the difference (4 - 3 = 1 hour).
    offset = config.SESSION_UTC_OFFSET_HOURS - config.SERVER_UTC_OFFSET_HOURS
    return (ts + timedelta(hours=offset)).strftime("%H:%M")


def get_symbol_info():
    info = mt5.symbol_info(config.SYMBOL)
    if info is None:
        raise RuntimeError(f"symbol_info() failed for {config.SYMBOL}: {mt5.last_error()}")
    return info


def get_filling_mode(info):
    fm = info.filling_mode
    for mode in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
        if fm & mode:
            return mode
    return mt5.ORDER_FILLING_FOK


def get_my_positions():
    return [p for p in mt5.positions_get(symbol=config.SYMBOL) if p.magic == config.MAGIC_NUMBER]


def send_market_order(side, lots, tp_price, sl_price, info):
    tick = mt5.symbol_info_tick(config.SYMBOL)
    if tick is None:
        raise RuntimeError("symbol_info_tick() failed")
    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if side == "BUY" else tick.bid
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": config.SYMBOL,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": sl_price if sl_price else 0.0,
        "tp": tp_price if tp_price else 0.0,
        "deviation": config.DEVIATION,
        "magic": config.MAGIC_NUMBER,
        "comment": "auto_bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": get_filling_mode(info),
    }
    return mt5.order_send(request)


def close_position(position):
    tick = mt5.symbol_info_tick(position.symbol)
    info = mt5.symbol_info(position.symbol)
    order_type = mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": position.ticket,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": order_type,
        "price": price,
        "deviation": config.DEVIATION,
        "magic": config.MAGIC_NUMBER,
        "comment": "auto_bot close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": get_filling_mode(info),
    }
    return mt5.order_send(request)


# ---------------- state ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return strategy.initial_state()


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def reconcile_state(state):
    positions = get_my_positions()
    if not positions:
        state.update(position=None, armed_exit=False, bars_held=0)
        return
    p = positions[0]
    direction = "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT"
    if state.get("position") != direction:
        state.update(position=direction, armed_exit=False, bars_held=0)


# ---------------- logging ----------------
def log_row(row):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in LOG_COLUMNS})


def beep(kind="info"):
    """Play a short tone for console events (Windows). Fails silently elsewhere."""
    try:
        import winsound
        if kind == "exit":
            winsound.Beep(1200, 150)
            winsound.Beep(900, 150)
            return
        tones = {
            "buy": (880, 250),
            "sell": (660, 250),
            "tp": (1000, 200),
            "sl": (400, 300),
            "bar": (700, 80),
        }
        freq, dur = tones.get(kind, (800, 150))
        winsound.Beep(freq, dur)
    except Exception:
        print("\a", end="", flush=True)


def is_news_time(now):
    """True if within NEWS_BUFFER_MIN of a known high-impact event (Dubai time)."""
    # Recurring: US unemployment claims (every Thu) and NFP (first Fri) — 8:30 ET = 16:30 Dubai.
    event_times = []
    if now.weekday() == 3:  # Thursday
        event_times.append(now.replace(hour=16, minute=30, second=0, microsecond=0))
    if now.weekday() == 4 and now.day <= 7:  # first Friday of the month
        event_times.append(now.replace(hour=16, minute=30, second=0, microsecond=0))
    for ts in event_times:
        if abs((now - ts).total_seconds()) <= NEWS_BUFFER_MIN * 60:
            return True
    for s in NEWS_SPECIFIC_DUBAI:
        try:
            ev = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            if abs((now - ev).total_seconds()) <= NEWS_BUFFER_MIN * 60:
                return True
        except Exception:
            pass
    return False


def run():
    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", f"console_{datetime.now(TZ).strftime('%Y%m%d')}.log")
    tee = ConsoleTee(log_path)
    sys.stdout = tee
    sys.stderr = tee

    threading.Thread(target=_watchdog, daemon=True).start()

    connect_with_retry()
    info = get_symbol_info()
    contract = info.trade_contract_size

    account = mt5.account_info()
    day_start_balance = account.balance if account else 0.0
    if account:
        print(f"[account] balance=${account.balance:.2f}  equity=${account.equity:.2f}  leverage=1:{account.leverage}")
    log_row({"timestamp": now_dubai().isoformat(timespec="seconds"),
             "event": "SESSION_START",
             "note": f"start balance ${day_start_balance:.2f}"})

    state = load_state()
    reconcile_state(state)
    last_bar_time = None
    trade = {}  # side / entry_price (for P&L reporting)
    cooldown = 0  # bars remaining before re-entry is allowed after a stop-loss

    print("=" * 78)
    print(f"{BOLD}AUTO BOT{RESET} — {config.SYMBOL} {config.TIMEFRAME}  {RED}(DEMO){RESET}")
    print(f"Rules : K/D cross entry (bar close) | fade exit | max {MAX_BARS} bars | both directions")
    print(f"Lot   : {LOT_SIZE}  |  TP half @ +${PARTIAL_TP_USD:.2f}  |  SL ${SL_USD:.2f}")
    print(f"Mode  : {'session ' + config.SESSION_START + '-' + config.SESSION_END + ' Dubai' if ENFORCE_SESSION else 'any time'}")
    print(f"{DIM}Live on the demo account. Ctrl+C to stop.{RESET}")
    print("=" * 78)

    try:
        while True:
            _heartbeat["t"] = time.time()
            now = now_dubai()
            t = now.time()

            if ENFORCE_SESSION:
                if t < parse_hhmm(config.SESSION_START):
                    print(f"[session] before start — waiting for {config.SESSION_START} Dubai")
                    time.sleep(60)
                    continue
                if t >= parse_hhmm(config.SESSION_END):
                    for p in get_my_positions():
                        r = close_position(p)
                        print(f"[session] flatten ticket={p.ticket} retcode={r.retcode}")
                    state.update(position=None, armed_exit=False, bars_held=0)
                    save_state(state)
                    acct = mt5.account_info()
                    end_balance = acct.balance if acct else day_start_balance
                    day_pnl = end_balance - day_start_balance
                    print(f"[session] end reached — day P&L ${day_pnl:+.2f} "
                          f"(${day_start_balance:.2f} -> ${end_balance:.2f})")
                    log_row({"timestamp": now.isoformat(timespec="seconds"),
                             "event": "SESSION_END",
                             "note": f"start ${day_start_balance:.2f} | end ${end_balance:.2f} | day P&L ${day_pnl:+.2f}"})
                    print("[session] stopping.")
                    break

            rates = mt5.copy_rates_from_pos(config.SYMBOL, config.mt5_timeframe(), 0, 300)
            if rates is None or len(rates) < 3:
                time.sleep(10)
                continue

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df["k"], df["d"] = indicators.compute_stoch_rsi(
                df["close"], config.RSI_PERIOD, config.STOCH_PERIOD, config.SMOOTH_K, config.SMOOTH_D,
            )

            # iloc[-1] = current FORMING bar, iloc[-2] = last COMPLETED bar.
            completed_bar_time = df["time"].iloc[-2]
            k_prev, d_prev = df["k"].iloc[-3], df["d"].iloc[-3]
            k_curr, d_curr = df["k"].iloc[-2], df["d"].iloc[-2]
            k_form, d_form = df["k"].iloc[-1], df["d"].iloc[-1]
            new_bar = (completed_bar_time != last_bar_time)

            if pd.isna(k_curr) or pd.isna(d_curr) or pd.isna(k_prev) or pd.isna(d_prev) or pd.isna(k_form) or pd.isna(d_form):
                time.sleep(10)
                continue

            if new_bar and cooldown > 0:
                cooldown -= 1

            bar_dubai = bar_dubai_str(completed_bar_time)

            # ATR volatility filter (consistency over total profit)
            atr_ok = True
            atr_note = ""
            if ATR_LOW_FACTOR > 0 or ATR_HIGH_FACTOR > 0:
                atr = indicators.compute_atr(df, 14)
                atr_med = atr.rolling(100, min_periods=1).median()
                a = atr.iloc[-2]
                m = atr_med.iloc[-2]
                if not pd.isna(a) and not pd.isna(m):
                    if ATR_LOW_FACTOR > 0 and a < m * ATR_LOW_FACTOR:
                        atr_ok = False
                        atr_note = f"low vol ({a:.2f} < {m * ATR_LOW_FACTOR:.2f})"
                    elif ATR_HIGH_FACTOR > 0 and a > m * ATR_HIGH_FACTOR:
                        atr_ok = False
                        atr_note = f"high vol ({a:.2f} > {m * ATR_HIGH_FACTOR:.2f})"

            # ADX regime filter (skip strong trends for a mean-reversion strategy)
            adx_ok = True
            adx_note = ""
            if ADX_THRESHOLD > 0:
                adx = indicators.compute_adx(df, 14)
                av = adx.iloc[-2]
                if not pd.isna(av) and av > ADX_THRESHOLD:
                    adx_ok = False
                    adx_note = f"trending (ADX {av:.0f} > {ADX_THRESHOLD:.0f})"

            # News filter (skip entries around high-impact events)
            news_ok = not is_news_time(now)

            # detect partial TP: one of the two halves closed (the other has no TP)
            positions = get_my_positions()
            if (trade and not trade.get("partial_done") and state["position"] is not None
                    and len(positions) == 1):
                trade["partial_done"] = True
                beep("tp")
                print(f"\n{DIM}{bar_dubai} | PARTIAL TP — half closed at +${PARTIAL_TP_USD:.2f}{RESET}\n")
                log_row({"timestamp": now.isoformat(timespec="seconds"), "bar_time": bar_dubai,
                         "event": "PARTIAL_TP", "side": state["position"],
                         "note": f"half closed at +${PARTIAL_TP_USD:.2f}"})

            # reconcile: SL closed the whole position server-side
            if state["position"] is not None and not positions:
                side = state["position"]
                beep("sl")
                print(f"\n{DIM}{bar_dubai} | SL filled server-side — closed {side}{RESET}\n")
                log_row({"timestamp": now.isoformat(timespec="seconds"), "bar_time": bar_dubai,
                         "event": "EXTERNAL_CLOSE", "side": side,
                         "note": "SL filled server-side"})
                state.update(position=None, armed_exit=False, bars_held=0)
                trade = {}
                cooldown = REARM_COOLDOWN

            tick = mt5.symbol_info_tick(config.SYMBOL)

            # realtime line — every poll (forming-bar values + live price)
            if tick is not None:
                rt_pos = state["position"] or "FLAT"
                rt_pc = GREEN if state["position"] == "LONG" else RED if state["position"] == "SHORT" else DIM
                rt_held = f" {state['bars_held']}/{MAX_BARS}" if state["position"] else ""
                rt_acct = mt5.account_info()
                rt_eq = rt_acct.equity if rt_acct else 0.0
                print(f"{DIM}{now.strftime('%H:%M:%S')}{RESET} | K={k_form:5.1f} D={d_form:5.1f} "
                      f"| {rt_pc}{rt_pos}{rt_held}{RESET} | bid {tick.bid:.2f} | eq ${rt_eq:,.2f}")

            # ---- on a NEW completed bar: evaluate entry + exit (bar-close, matches backtest) ----
            if new_bar:
                beep("bar")
                last_bar_time = completed_bar_time
                acct = mt5.account_info()
                equity = acct.equity if acct else 0.0

                state_before = dict(state)
                prev_bars = state["bars_held"]
                sig = strategy.evaluate_bar(
                    k_prev, d_prev, k_curr, d_curr, state,
                    entry_mode=ENTRY_MODE, exit_mode=EXIT_MODE,
                    use_ma_filter=USE_MA_FILTER, max_bars_held=MAX_BARS,
                    direction=DIRECTION,
                )

                pos = state["position"] or "FLAT"
                held = f" {state['bars_held']}/{MAX_BARS}" if state["position"] else ""
                pc = GREEN if state["position"] == "LONG" else RED if state["position"] == "SHORT" else DIM
                print(f"{DIM}{bar_dubai}{RESET} | K={k_curr:5.1f} D={d_curr:5.1f} | {pc}{pos}{held}{RESET} "
                      f"| equity ${equity:,.2f}")

                # ---- ENTRY ----
                if sig in ("BUY", "SELL"):
                    blocked = ""
                    if not atr_ok:
                        blocked = f"ATR ({atr_note})"
                    elif not adx_ok:
                        blocked = f"ADX ({adx_note})"
                    elif not news_ok:
                        blocked = "news window"
                    elif cooldown > 0:
                        blocked = f"cooldown {cooldown} bars"
                    if blocked:
                        print(f"{DIM}{bar_dubai} | {sig} blocked: {blocked}{RESET}")
                        state.update(state_before)
                    else:
                        side = "LONG" if sig == "BUY" else "SHORT"
                        color = GREEN if sig == "BUY" else RED
                        entry = tick.ask if sig == "BUY" else tick.bid
                        sl = (entry - SL_USD) if sig == "BUY" else (entry + SL_USD)
                        tp_half = (entry + PARTIAL_TP_USD) if sig == "BUY" else (entry - PARTIAL_TP_USD)

                        r1 = send_market_order(sig, HALF_LOT, tp_half, sl, info)
                        r2 = send_market_order(sig, HALF_LOT, 0.0, sl, info)
                        ok = (r1.retcode == mt5.TRADE_RETCODE_DONE and
                              r2.retcode == mt5.TRADE_RETCODE_DONE)
                        fill = r1.price if r1.price else entry
                        bar_open = float(df["open"].iloc[-1])
                        fill_gap = (fill - bar_open) if sig == "BUY" else (bar_open - fill)

                        print()
                        print(color + BOLD + "═" * 78 + RESET)
                        print(f"{color}{BOLD}  {sig}{RESET}  {bar_dubai} Dubai  {color}{BOLD}→  OPEN {side}{RESET}"
                              if ok else
                              f"{RED}{BOLD}  {sig} FAILED{RESET}  {bar_dubai} Dubai  {RED}retcode={r1.retcode}/{r2.retcode}{RESET}")
                        print(color + BOLD + "═" * 78 + RESET)
                        print(f"  {CYAN}K {'UP' if sig == 'BUY' else 'DOWN'} through D in "
                              f"{'oversold' if sig == 'BUY' else 'overbought'}  |  K {k_prev:.1f}→{k_curr:.1f}  D {d_curr:.1f}{RESET}")
                        print(f"  Entry  {entry:.2f}  |  SL {sl:.2f}  |  Lot {LOT_SIZE} (half TP @ +${PARTIAL_TP_USD:.2f})")
                        if ok:
                            print(f"  Filled {fill:.2f}  |  Tickets #{getattr(r1, 'order', '')} & #{getattr(r2, 'order', '')}")
                            print(f"  {DIM}bar open {bar_open:.2f}  |  fill gap {fill_gap:+.2f} (adverse if +){RESET}")
                        else:
                            print(f"  {RED}Order rejected: {r1.comment} / {r2.comment}{RESET}")
                        print(color + BOLD + "═" * 78 + RESET)
                        print()

                        log_row({"timestamp": now.isoformat(timespec="seconds"), "bar_time": bar_dubai,
                                 "event": "ENTRY" if ok else "ENTRY_FAIL", "side": side,
                                 "k": round(float(k_curr), 2), "d": round(float(d_curr), 2),
                                 "price": round(float(fill), 2), "lots": LOT_SIZE,
                                 "ticket": f"{getattr(r1, 'order', '')},{getattr(r2, 'order', '')}",
                                 "bar_open": round(bar_open, 2), "fill_gap": round(float(fill_gap), 2),
                                 "note": f"retcode={r1.retcode}/{r2.retcode}"})
                        if ok:
                            beep("buy" if sig == "BUY" else "sell")
                            state.update(position="LONG" if sig == "BUY" else "SHORT",
                                         armed_exit=False, bars_held=0)
                            trade = {"side": side, "entry_price": fill, "partial_done": False}
                        else:
                            for p in get_my_positions():
                                close_position(p)
                            state.update(position=None, armed_exit=False, bars_held=0)

                # ---- EXIT ----
                elif sig in ("CLOSE_LONG", "CLOSE_SHORT"):
                    side = "LONG" if sig == "CLOSE_LONG" else "SHORT"
                    target = mt5.POSITION_TYPE_BUY if sig == "CLOSE_LONG" else mt5.POSITION_TYPE_SELL
                    held_bars = prev_bars + 1
                    reason = ("D crossed back out of opposite extreme" if held_bars < MAX_BARS
                              else f"held full {MAX_BARS} bars (time stop)")
                    for p in positions:
                        if p.type == target:
                            r = close_position(p)
                            close_price = tick.bid if sig == "CLOSE_LONG" else tick.ask
                            bar_open = float(df["open"].iloc[-1])
                            fill_gap = (bar_open - close_price) if sig == "CLOSE_LONG" else (close_price - bar_open)
                            pnl = None
                            if trade.get("entry_price"):
                                diff = (close_price - trade["entry_price"]) if side == "LONG" \
                                    else (trade["entry_price"] - close_price)
                                pnl = diff * contract * p.volume
                            print()
                            print(YELLOW + BOLD + "═" * 78 + RESET)
                            print(f"{YELLOW}{BOLD}  CLOSE {side}{RESET}  {bar_dubai} Dubai")
                            print(YELLOW + BOLD + "═" * 78 + RESET)
                            print(f"  Reason  {reason}")
                            print(f"  Close   {close_price:.2f}  |  Bars {held_bars} ({held_bars * 5} min)")
                            print(f"  {DIM}bar open {bar_open:.2f}  |  fill gap {fill_gap:+.2f} (adverse if +){RESET}")
                            if pnl is not None:
                                print(f"  {GREEN if pnl >= 0 else RED}P&L     ${pnl:+.2f}{RESET}")
                            print(f"  retcode {r.retcode} {r.comment}")
                            print(YELLOW + BOLD + "═" * 78 + RESET)
                            print()
                            log_row({"timestamp": now.isoformat(timespec="seconds"), "bar_time": bar_dubai,
                                     "event": "EXIT", "side": side,
                                     "k": round(float(k_curr), 2), "d": round(float(d_curr), 2),
                                     "price": round(float(close_price), 2), "lots": p.volume,
                                     "ticket": p.ticket,
                                     "bar_open": round(bar_open, 2), "fill_gap": round(float(fill_gap), 2),
                                     "note": f"bars={held_bars} retcode={r.retcode}"})
                    beep("exit")
                    trade = {}

            save_state(state)

            # Align to bar close: fire orders ~1s after each boundary instead of
            # up to POLL_SEC late. Gold drifts fast, and that drift is the
            # remaining live-vs-backtest gap. Mid-bar we still wake every
            # POLL_SEC for the realtime line.
            secs = time.time() % 300
            if secs < 3.0:
                time.sleep(0.2)
            else:
                time.sleep(min(float(POLL_SEC), 300.0 - secs + 1.0))

    except KeyboardInterrupt:
        acct = mt5.account_info()
        if acct:
            end_balance = acct.balance
            day_pnl = end_balance - day_start_balance
            print(f"\n[bot] stopped. balance=${acct.balance:.2f} equity=${acct.equity:.2f}")
            print(f"[bot] day P&L ${day_pnl:+.2f} (${day_start_balance:.2f} -> ${end_balance:.2f})")
            log_row({"timestamp": datetime.now(TZ).isoformat(timespec="seconds"),
                     "event": "BOT_STOP",
                     "note": f"start ${day_start_balance:.2f} | end ${end_balance:.2f} | day P&L ${day_pnl:+.2f}"})
        print("[bot] open positions (if any) remain on the account.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    run()
