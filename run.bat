@echo off
REM SPDX-License-Identifier: BSD-3-Clause
REM Dev launcher (no install needed): pip install -r requirements.txt, then run.bat

echo Starting OntoMonster...

REM Try to find Python
where python >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=python
) else (
    where python3 >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON=python3
    ) else (
        echo Error: Python not found. Please install Python 3.8 or later.
        pause
        exit /b 1
    )
)

set PYTHONPATH=src
%PYTHON% -m ontomonster %*
