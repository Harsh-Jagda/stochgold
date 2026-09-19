"""
indicators.py — RSI, Stochastic RSI, MA. Pure functions, no MT5 calls.

These formulas were confirmed against the working MT5 custom Stochastic RSI
indicator (AGENTS.md Section 5). Implement exactly as written — do not substitute
a TA library's version, since library implementations can use different smoothing
conventions and silently diverge.

IMPORTANT: never read indicator values from the MT5 terminal. The Python package
has no equivalent of MQL5's iCustom(); all indicator values are computed here from
raw OHLC data (mt5.copy_rates_from_pos()).
"""
import pandas as pd


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's smoothing RSI — matches MT5's native RSI method."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_stoch_rsi(close: pd.Series, rsi_period: int = 14, stoch_period: int = 8,
                      smooth_k: int = 3, smooth_d: int = 3):
    """Applies the Stochastic formula to RSI values (not price), then double-smooths."""
    rsi = compute_rsi(close, rsi_period)
    lowest = rsi.rolling(stoch_period).min()
    highest = rsi.rolling(stoch_period).max()
    stoch_raw = (rsi - lowest) / (highest - lowest) * 100
    k = stoch_raw.rolling(smooth_k).mean()   # SmoothK
    d = k.rolling(smooth_d).mean()           # SmoothD
    return k, d


def compute_ma(close: pd.Series, period: int = 13, method: str = "sma") -> pd.Series:
    if method == "ema":
        return close.ewm(span=period, adjust=False).mean()
    return close.rolling(period).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range — the standard volatility measure."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX — trend strength (low = ranging, high = trending)."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
