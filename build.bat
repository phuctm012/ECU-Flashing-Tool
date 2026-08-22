@echo off
REM ==================================================
REM FFlash — Build script (Windows only)
REM ==================================================
REM
REM Builds main.py (the GUI app) into a single standalone
REM FFlash.exe using PyInstaller. Run this from a Windows
REM machine with Python + the project's conda env active
REM (see README.md: "conda activate pyside6").
REM
REM Usage:
REM   build.bat
REM ==================================================

setlocal

cd /d "%~dp0"

set APP_NAME=FFlash
set ICON_PATH=resources\icon.ico

echo ==================================================
echo  %APP_NAME% - Build .exe
echo ==================================================
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
if exist "%ICON_PATH%" set PYI_ICON_ARG=--icon "%ICON_PATH%"

python -m PyInstaller --noconfirm --clean --onefile --windowed --name "%APP_NAME%" %PYI_ICON_ARG% main.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo ==================================================
echo  Build OK: dist\%APP_NAME%.exe
echo ==================================================

endlocal
