"""
strategy.py - the entry/exit state machine (single source of truth).

Final config (locked after extended walk-forward + Optuna):
  * Entry: "kd_cross" - K crosses D inside the extreme zone (long in oversold,
           short in overbought), evaluated at bar close.
  * Exit : "fade" - D crosses back OUT of the opposite extreme (long: D drops
           back below the upper limit; short: D climbs back above the lower),
           evaluated at bar close.

Safety nets:
  * MIN_BARS_BEFORE_EXIT - ignore exits for a few bars right after entry.
  * MAX_BARS_HELD        - force-close a position that never reaches the opposite extreme.

evaluate_bar() is the single per-bar function. backtest.py and auto_bot.py both
call it, so backtest and live math are identical by construction.
"""
import numpy as np
import pandas as pd

import config


def initial_state():
    """Fresh state for the signal state machine."""
    return {"position": None, "armed_exit": False, "bars_held": 0}


def _ma_trend_ok(direction, use_ma_filter, close_curr, ma_curr):
    """MA13 trend filter: longs only above MA, shorts only below MA."""
    if not use_ma_filter or close_curr is None or ma_curr is None:
        return True
    if pd.isna(close_curr) or pd.isna(ma_curr):
        return True
    return close_curr > ma_curr if direction == "BUY" else close_curr < ma_curr


def evaluate_bar(k_prev, d_prev, k_curr, d_curr, state,
                 lower_limit=config.LOWER_LIMIT,
                 upper_limit=config.UPPER_LIMIT,
                 min_bars_before_exit=config.MIN_BARS_BEFORE_EXIT,
                 max_bars_held=config.MAX_BARS_HELD,
                 use_ma_filter=False, close_curr=None, ma_curr=None,
                 entry_mode="d_limit", exit_mode="cross_dir", k_confirm=False,
                 direction="both", depth_extra=0.0):
    """
    Evaluate one bar transition. Mutates `state` in place and returns the signal
    string in {'BUY', 'SELL', 'CLOSE_LONG', 'CLOSE_SHORT'} or None.

    Directional K/D crosses (the user said direction matters):
      * bull_cross: K crosses UP through D (below -> above).
      * bear_cross: K crosses DOWN through D (above -> below).

    entry_mode:
      * "d_limit" : D crosses UP through lower (long) / DOWN through upper (short).
      * "kd_cross": bull_cross in oversold (long) / bear_cross in overbought (short).

    exit_mode:
      * "cross_dir": opposite directional cross in the opposite extreme.
      * "touch"    : D just reaches the opposite extreme (earliest).
      * "fade"     : D crosses BACK OUT of the opposite extreme.
    """
    position = state["position"]
    bars_held = state["bars_held"]
    signal = None

    # Directional K/D crosses with a tolerance: at the extremes (0/100) K and D
    # get pinned together and float noise can make one a hair above/below the
    # other, which would otherwise swallow a real crossover.
    EPS = 1e-3
    prev_diff = k_prev - d_prev
    curr_diff = k_curr - d_curr
    prev_sign = 0 if abs(prev_diff) < EPS else (1 if prev_diff > 0 else -1)
    curr_sign = 0 if abs(curr_diff) < EPS else (1 if curr_diff > 0 else -1)
    bull_cross = (prev_sign <= 0) and (curr_sign > 0)   # K crosses UP through D
    bear_cross = (prev_sign >= 0) and (curr_sign < 0)   # K crosses DOWN through D

    if position is None:
        if entry_mode == "kd_cross":
            long_sig = bull_cross and d_curr <= (lower_limit - depth_extra)
            short_sig = bear_cross and d_curr >= (upper_limit + depth_extra)
        elif entry_mode == "kd_state":
            # no fresh cross required — K just needs to be on the right side of D
            # in the extreme zone (catches reversals, but can re-buy falling knives)
            long_sig = (k_curr > d_curr) and d_curr <= (lower_limit - depth_extra)
            short_sig = (k_curr < d_curr) and d_curr >= (upper_limit + depth_extra)
        else:  # d_limit
            long_sig = d_prev < lower_limit and d_curr >= lower_limit
            short_sig = d_prev > upper_limit and d_curr <= upper_limit

        if direction == "long":
            short_sig = False
        elif direction == "short":
            long_sig = False

        if long_sig:
            ok = _ma_trend_ok("BUY", use_ma_filter, close_curr, ma_curr)
            if k_confirm and not k_curr > k_prev:
                ok = False
            if ok:
                signal = "BUY"
                state.update(position="LONG", armed_exit=False, bars_held=0)
        elif short_sig:
            ok = _ma_trend_ok("SELL", use_ma_filter, close_curr, ma_curr)
            if k_confirm and not k_curr < k_prev:
                ok = False
            if ok:
                signal = "SELL"
                state.update(position="SHORT", armed_exit=False, bars_held=0)

    elif position == "LONG":
        bars_held += 1
        state["bars_held"] = bars_held
        if bars_held >= max_bars_held:
            signal = "CLOSE_LONG"
            state.update(position=None, armed_exit=False, bars_held=0)
        elif bars_held >= min_bars_before_exit:
            if exit_mode == "reversal":
                # flip straight to a SHORT when a short entry fires (bear cross in overbought)
                if bear_cross and d_curr >= upper_limit:
                    signal = "SELL"
                    state.update(position="SHORT", armed_exit=False, bars_held=0)
            else:
                if exit_mode == "touch":
                    exit_ok = d_curr >= upper_limit
                elif exit_mode == "fade":
                    exit_ok = d_prev >= upper_limit and d_curr < upper_limit
                elif exit_mode == "rollover":
                    exit_ok = d_curr < d_prev and d_curr >= upper_limit
                else:  # cross_dir
                    exit_ok = bear_cross and d_curr >= upper_limit
                if exit_ok:
                    signal = "CLOSE_LONG"
                    state.update(position=None, armed_exit=False, bars_held=0)

    elif position == "SHORT":
        bars_held += 1
        state["bars_held"] = bars_held
        if bars_held >= max_bars_held:
            signal = "CLOSE_SHORT"
            state.update(position=None, armed_exit=False, bars_held=0)
        elif bars_held >= min_bars_before_exit:
            if exit_mode == "reversal":
                # flip straight to a LONG when a long entry fires (bull cross in oversold)
                if bull_cross and d_curr <= lower_limit:
                    signal = "BUY"
                    state.update(position="LONG", armed_exit=False, bars_held=0)
            else:
                if exit_mode == "touch":
                    exit_ok = d_curr <= lower_limit
                elif exit_mode == "fade":
                    exit_ok = d_prev <= lower_limit and d_curr > lower_limit
                elif exit_mode == "rollover":
                    exit_ok = d_curr > d_prev and d_curr <= lower_limit
                else:  # cross_dir
                    exit_ok = bull_cross and d_curr <= lower_limit
                if exit_ok:
                    signal = "CLOSE_SHORT"
                    state.update(position=None, armed_exit=False, bars_held=0)

    return signal
