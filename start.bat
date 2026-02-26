@echo off
REM start.bat - ensure uv tool installed, venv synced, then activate and run app

echo Checking for uv executable...
where uv >nul 2>&1
if errorlevel 1 (
    echo uv not found. Installing uv...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 (
        echo Failed to install uv. Please install manually and re-run.
        exit /b 1
    )
) else (
    echo uv is already installed.
)

echo Verifying virtual environment...
if not exist "%~dp0\.venv\Scripts\activate" (
    echo Virtual environment not found, running uv sync to create it...
    uv sync
    if errorlevel 1 (
        echo uv sync failed. Please check uv configuration.
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)

echo Activating virtual environment...
call "%~dp0\.venv\Scripts\activate"

echo Starting application...
python "%~dp0\main.py"
