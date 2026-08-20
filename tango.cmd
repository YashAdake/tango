@echo off
REM Launcher so `tango status` works from anywhere, not just this directory.
REM Add this folder to PATH, or copy this file somewhere already on it.
setlocal
set "TANGO_HOME=%~dp0"
if exist "%TANGO_HOME%.venv\Scripts\python.exe" (
    "%TANGO_HOME%.venv\Scripts\python.exe" -m tango.cli --db "%TANGO_HOME%data\tango.db" --playbooks "%TANGO_HOME%playbooks" --hosts "%TANGO_HOME%hosts" %*
) else (
    echo Tango's virtualenv is missing. From %TANGO_HOME%:
    echo   uv venv --python 3.12 ^&^& .venv\Scripts\activate ^&^& uv pip install -e ".[dev]"
    exit /b 1
)
