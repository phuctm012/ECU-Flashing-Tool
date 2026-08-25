@echo off
REM ==================================================
REM SFlash — Build script (Windows only)
REM ==================================================
REM
REM Builds main.py (the GUI app) with PyInstaller. Run this
REM from a Windows machine with Python + the project's conda
REM env active (see README.md: "conda activate pyside6").
REM Prompts interactively for Onefile (single .exe, easiest to
REM share, slower to start) vs Onedir (a folder, starts faster,
REM must be kept together) — see the prompt text below.
REM
REM Usage:
REM   build.bat
REM ==================================================

setlocal

cd /d "%~dp0"

set APP_NAME=SFlash
set ICON_PATH=resources\icons\flash_bolt_blue.ico

echo ==================================================
echo  %APP_NAME% - Build .exe
echo ==================================================
echo.

echo Choose build type:
echo   1. Onefile - single .exe, easiest to share, but self-extracts
echo      to a temp folder on every launch (slower to start, worse
echo      on slow disks / with antivirus scanning the extracted files).
echo   2. Onedir  - a folder (SFlash.exe + its support files together),
echo      starts noticeably faster since there is no self-extract step,
echo      but you must keep/copy the whole folder together, not just
echo      the .exe.
echo.
set PYI_MODE_ARG=--onefile
set PYI_MODE_LABEL=onefile
set /p BUILD_CHOICE="Enter 1 or 2 (default 1 - Onefile): "
if "%BUILD_CHOICE%"=="2" (
    set PYI_MODE_ARG=--onedir
    set PYI_MODE_LABEL=onedir
)
echo Building as: %PYI_MODE_LABEL%
echo.

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] "python" not found on PATH.
    echo Activate the project's Python/conda environment first, e.g.: conda activate pyside6
    echo Then re-run build.bat.
    exit /b 1
)

echo [1/3] Installing build dependencies...
python -m pip install -r requirements.txt -r requirements_build.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip install failed.
    exit /b 1
)
echo.

echo [2/3] Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"
echo.

echo [3/3] Running PyInstaller...
set PYI_ICON_ARG=
if exist "%ICON_PATH%" (
    set PYI_ICON_ARG=--icon "%ICON_PATH%"
    echo Using icon: %ICON_PATH%
) else (
    echo [WARN] Icon not found at %ICON_PATH% - building without a custom icon.
)

python -m PyInstaller --noconfirm --clean %PYI_MODE_ARG% --windowed --name "%APP_NAME%" --add-data "docs\user_guide.html;docs" --add-data "resources\style.qss;resources" --add-data "resources\style_dark.qss;resources" --add-data "resources\icons;resources\icons" %PYI_ICON_ARG% main.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo ==================================================
if "%PYI_MODE_LABEL%"=="onedir" (
    echo  Build OK ^(onedir^): dist\%APP_NAME%\%APP_NAME%.exe
    echo  Keep the whole dist\%APP_NAME%\ folder together when copying it.
) else (
    echo  Build OK ^(onefile^): dist\%APP_NAME%.exe
)
echo ==================================================

endlocal
