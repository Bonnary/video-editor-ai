@echo off
REM start.bat - ensure uv tool installed, venv synced, then activate and run app

:: ensure scoop is available so we can install ffmpeg if needed
echo Checking for scoop (package manager)...
where scoop >nul 2>&1
if errorlevel 1 (
    echo scoop not found. Attempting to install via PowerShell...
    powershell -NoProfile -ExecutionPolicy RemoteSigned -Command "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser; iex ((New-Object System.Net.WebClient).DownloadString('https://get.scoop.sh'))"
    if errorlevel 1 (
        echo Failed to install scoop.  You may need to run the above commands manually and re-run start.bat.
    ) else (
        echo scoop installed successfully.
    )
) else (
    echo scoop is already installed.
)

:: if ffmpeg is missing, use scoop to install it (scoop will ignore if already installed)
echo Checking for ffmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo ffmpeg not found, attempting to install via scoop...
    scoop install ffmpeg || echo "scoop install ffmpeg" failed; please install ffmpeg manually.
) else (
    echo ffmpeg is already available.
)

:: Now handle uv, reinstalling the script if needed after uv install
echo Checking for uv executable...
where uv >nul 2>&1
if errorlevel 1 (
    echo uv not found. Installing uv...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 (
        echo Failed to install uv. Please install manually and re-run.
        exit /b 1
    ) else (
        echo uv installed; restarting this script to pick up new PATH.
        call "%~f0" %*
        exit /b
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
