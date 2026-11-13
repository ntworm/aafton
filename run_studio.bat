@echo off
title aafton
cd /d "%~dp0"
set PYTHONPATH=src
"%~dp0.venv\Scripts\python.exe" -m aafton.cli ui
pause
