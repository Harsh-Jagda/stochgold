@echo off
title Stoch RSI Bot - auto restart loop
echo.
echo Starting bot. It will auto-restart whenever it stops (session end or crash).
echo Close this window (or Ctrl+C) to stop permanently.
echo.

:loop
python auto_bot.py
echo.
echo Bot stopped. Restarting in 60 seconds...
timeout /t 60 /nobreak >nul
goto loop
