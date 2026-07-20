@echo off
rem Launcher only. All logic and Japanese messages live in crawl_chobirich.ps1,
rem because cmd misparses batch files that mix "chcp 65001" with multibyte text
rem (it loses its read position after external commands and executes comment
rem fragments). Keep this file ASCII-only.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0crawl_chobirich.ps1"
exit /b %ERRORLEVEL%
