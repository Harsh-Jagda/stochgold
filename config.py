"""
config.py - single source of truth for tunable parameters.

The Stochastic RSI values below were validated against a working MT5 custom
indicator and a TradingView reference chart. Don't change them without
re-validating. Day-to-day knobs (SL, partial TP, session enforcement, filters)
live at the top of auto_bot.py.
"""

# --- Instrument & timeframe ---
SYMBOL = "XAUUSD"
TIMEFRAME = "M5"              # maps to mt5.TIMEFRAME_M5 via mt5_timeframe()

# --- Stochastic RSI parameters ---
# Validated: matched against a working MT5 custom indicator AND a TradingView
# reference chart before being accepted. Do not change without re-validating.
RSI_PERIOD   = 21
STOCH_PERIOD = 8
SMOOTH_K     = 3
SMOOTH_D     = 3
UPPER_LIMIT  = 80
LOWER_LIMIT  = 23

# --- Trend filter ---
MA_PERIOD = 13
MA_METHOD = "sma"             # decided: SMA (user)
USE_MA_FILTER = False         # MA13 trend filter (OFF: extended backtest found it hurt consistency)

# --- Strategy safety nets (see AGENTS.md Section 6 for rationale) ---
MIN_BARS_BEFORE_EXIT = 3      # ignore exit conditions for this many bars right after entry
MAX_BARS_HELD = 15            # force-close if still open after this many bars

# --- Risk management (user decisions) ---
RISK_USD = 8.0                # fixed dollars risked per trade (user confirmed: 0.8% of $1000)
STOP_LOSS_DISTANCE_USD = 8.0  # SL placed this far from entry, in price dollars
                              #   -> XAUUSD contract = 100 oz, so 0.01 lot x $8.00 = $8.00 loss if SL hits.
# ACCOUNT_RISK_PCT / leverage remain open decisions, but with fixed-dollar risk they
# no longer drive position sizing (leverage affects margin only, not P&L or lot size).

# --- Session control (local machine, no VPS — bot runs ~2-3 hrs/day) ---
SESSION_START = "15:00"       # Dubai time (UTC+4, no DST) — profitable session window
SESSION_END   = "20:00"
SESSION_UTC_OFFSET_HOURS = 4  # Dubai = UTC+4, no daylight saving
NO_NEW_TRADES_BUFFER_MIN = 25 # stop opening NEW positions this many minutes before SESSION_END
SESSION_FILTER_BACKTEST = True # backtest: only take entries inside the session window (fair test for a session method)
SERVER_UTC_OFFSET_HOURS = 3    # fallback MT5 server offset from UTC (auto-detected at runtime)

# --- Execution ---
MAGIC_NUMBER = 123456         # unique ID so the bot only manages its own trades
DEVIATION = 20                # max allowed slippage, in points

# --- Backtest (used only by backtest.py) ---
BACKTEST_START = "2025-01-01"
BACKTEST_END   = "2026-08-27"
BACKTEST_LOT_SIZE = 0.01      # fixed lot size for P&L simulation
BACKTEST_SPREAD_POINTS = None # None -> use historical per-bar spread from MT5 data
BACKTEST_SLIPPAGE_POINTS = 10 # small slippage assumption, in points


def mt5_timeframe():
    """Map the TIMEFRAME label to the MetaTrader5 constant (lazy import)."""
    import MetaTrader5 as mt5
    return getattr(mt5, f"TIMEFRAME_{TIMEFRAME}")
