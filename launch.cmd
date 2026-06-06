@echo off
REM hermes_cu v0.2.0 MCP launcher for Hermes.
REM hermes-agent MCP tool.py spawns:  command [args...]
REM We always run "hermes_cu serve" (MCP mode). Arguments passed through %* are
REM appended, but hermes_cu serve takes no extra args so this is safe.
REM The PYTHONPATH must point to the parent of the hermes_cu/ package.
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=D:\Hermes_Backup\github\hermes_cu"
"C:\Users\CLL\.hermes\hermes-agent\venv\Scripts\python.exe" -m hermes_cu serve
endlocal
