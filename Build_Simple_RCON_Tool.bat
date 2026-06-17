@echo off
rem ===========================================================================
rem  Build Simple RCON Tool        JDE-Projects (https://github.com/JDE-Projects)
rem
rem  Double-click to build a windowed "Simple RCON Tool" with PyInstaller.
rem  Closed / noncommercial build, so this uses --onedir (the Qt and other
rem  bundled libraries stay replaceable). Keep this .bat in the SAME folder as
rem  simple_rcon_tool.py, simple_rcon_tool-UI.html, the fonts folder,
rem  simple_rcon_tool.ico, simple_rcon_tool.png and simple_rcon_tool-splash.png.
rem  The finished app lands in dist\Simple RCON Tool\.
rem ===========================================================================
cd /d "%~dp0"

rem --- skip interactive pauses when running in CI (GitHub Actions sets CI) ---
set "PAUSE=pause"
if defined CI set "PAUSE="

rem --- bind to PySide6 (LGPL), not PyQt6 (GPL) ---
set QT_API=pyside6

rem --- check Python ---
where python >nul 2>&1
if not %errorlevel%==0 (
    echo Python was not found on PATH.
    echo Install Python 3 from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" during setup.
    echo.
    %PAUSE%
    exit /b 1
)

rem --- make sure the source and assets are here ---
if not exist "simple_rcon_tool.py" (
    echo Could not find simple_rcon_tool.py next to this script.
    echo Put this .bat in the same folder as the source and asset files.
    echo.
    %PAUSE%
    exit /b 1
)
if not exist "simple_rcon_tool.ico" echo WARNING: simple_rcon_tool.ico not found, the exe will use the default icon.
if not exist "simple_rcon_tool.png" echo WARNING: simple_rcon_tool.png not found, the taskbar icon may be generic.
if not exist "simple_rcon_tool-splash.png" echo WARNING: simple_rcon_tool-splash.png not found, no splash will show.
if not exist "fonts" echo WARNING: fonts folder not found, the window will fall back to system fonts.

rem --- make sure PyInstaller is available, install if missing ---
python -m PyInstaller --version >nul 2>&1
if not %errorlevel%==0 (
    echo PyInstaller not found. Installing pinned dependencies now...
    python -m pip install -r requirements.txt
    if not %errorlevel%==0 (
        echo Could not install PyInstaller. Check pip/network and try again.
        echo.
        %PAUSE%
        exit /b 1
    )
)

rem --- make sure the runtime deps are present (pinned in requirements.txt) ---
echo Ensuring pinned dependencies from requirements.txt are installed ...
python -m pip install -r requirements.txt
if not %errorlevel%==0 (
    echo Could not install dependencies from requirements.txt. Check pip/network and try again.
    echo.
    %PAUSE%
    exit /b 1
)

rem --- clean previous output for a fresh build ---
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "Simple RCON Tool.spec" del /q "Simple RCON Tool.spec"

echo.
echo Building Simple RCON Tool ... this can take a minute.
echo.

python -m PyInstaller --noconfirm --onedir --windowed ^
    --name "Simple RCON Tool" ^
    --icon "simple_rcon_tool.ico" ^
    --splash "simple_rcon_tool-splash.png" ^
    --add-data "simple_rcon_tool-UI.html;." ^
    --add-data "simple_rcon_tool.png;." ^
    --add-data "fonts;fonts" ^
    --collect-all PySide6 ^
    --collect-all qtpy ^
    simple_rcon_tool.py

if not %errorlevel%==0 (
    echo.
    echo Build failed. Read the last lines above for the cause.
    echo.
    %PAUSE%
    exit /b 1
)

echo.
echo ===========================================================================
echo  Done. Your app folder is:  dist\Simple RCON Tool\
echo  Run dist\Simple RCON Tool\Simple RCON Tool.exe to test, then zip the
echo  whole "Simple RCON Tool" folder and attach it to the repo's Releases page.
echo ===========================================================================
echo.
%PAUSE%
