Not financial advice. Just something I built and what it taught me.

The bot trades gold (XAUUSD) on the 5 minute chart off a Stochastic RSI signal. Buy oversold, sell overbought, close when momentum fades. Mean reversion.

And it's been working. The last 35 days made about $170 per 0.01 lot, and the backtest has been green since 2025.

But here's the part I'm actually proud of. I didn't just find a window that looked good and ship it.

I hand rolled every indicator so the backtest and the live bot run the exact same math. I ran walk forward folds. I let Optuna search the full parameter space. I tested six different exit rules. I found and killed look-ahead bias in my own backtest, fills at prices you couldn't actually trade. I matched live to backtest so there was no "live is different" excuse.

Then I ran it on 2.7 years of data and found the thing most people never look for: the edge is regime dependent. It prints when gold ranges. When the market trends, the bot stands aside instead of fighting it.

So it's not a strategy that wins everywhere. Those don't exist. It's a strategy that knows when it should be trading, and that's the difference between a lucky backtest and something you can actually run.

Still demo. Still learning. But the process caught what most retail never checks.

Repo in comments.
