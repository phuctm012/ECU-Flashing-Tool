@echo off
REM ==================================================
REM SFlash CLI - Build script (Windows only)
REM ==================================================
REM
REM Builds cli.py into sflash-cli.exe for a machine that has no
REM Python - a bare CI runner, or a test bench PC. On a machine
REM that already has the project's conda env, do NOT use this:
REM "python cli.py ..." is lighter, starts faster and is far
REM easier to debug. See README.md, "Dung trong CI/CD pipeline".
REM
REM Two deliberate differences from build.bat (the GUI build),
REM both of which would break a CLI if copied from it:
REM
REM   --console (not --windowed). --windowed detaches the
REM   process from its console, so stdout/stderr go nowhere and
REM   a pipeline sees no log and no error text at all.
REM
REM   --onedir (not --onefile). --onefile self-extracts the whole
REM   bundle to a temp folder on EVERY launch. For a GUI started
REM   once that is a one-off delay; for a pipeline step invoked
REM   repeatedly it is paid every time, and antivirus rescans the
REM   extracted files each run.
REM
REM Size warning: cli.py imports PySide6 (it uses Qt's signal/
REM slot mechanism, not widgets), so the bundle carries all of
REM Qt - expect roughly 150-250 MB. That is the price of a
REM Python-free machine; it is not a packaging mistake.
REM
REM What is NOT bundled, by design:
REM   * The Security Access DLL. It is chosen at run time and
REM     stays an external file - see README.md. (A frozen build's
REM     ctypes hook used to disguise every DLL load failure as
REM     "not found when the application was frozen";
REM     communication/security_dll.py now unwraps that.)
REM   * python-can's Vector backend needs the Vector XL Driver
REM     Library installed on the machine, frozen or not.
REM
REM Usage:
REM   build_cli.bat
REM ==================================================

setlocal

cd /d "%~dp0"

set CLI_NAME=sflash-cli

echo ==================================================
echo  %CLI_NAME% - Build CLI .exe
echo ==================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] "python" not found on PATH.
    echo Activate the project's Python/conda environment first, e.g.: conda activate pyside6
    echo Then re-run build_cli.bat.
    exit /b 1
)

echo [1/4] Installing build dependencies...
python -m pip install -r requirements.txt -r requirements_build.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip install failed.
    exit /b 1
)
echo.

echo [2/4] Checking optional dependencies...
python -c "import can" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [WARN] python-can is not installed in this environment.
    echo        The built .exe will run the Virtual ECU Simulator fine
    echo        but will NEVER detect real Vector hardware, on any machine.
    echo        Install it first if the pipeline flashes real ECUs:
    echo            pip install python-can
    echo.
)
python -c "import gitlab" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [WARN] python-gitlab is not installed - the "gitlab" subcommands
    echo        will not work in the built .exe. Install it first if the
    echo        pipeline fetches firmware from GitLab:
    echo            pip install python-gitlab
    echo.
)

echo [3/4] Cleaning previous CLI build output...
if exist "build\%CLI_NAME%" rmdir /s /q "build\%CLI_NAME%"
if exist "dist\%CLI_NAME%" rmdir /s /q "dist\%CLI_NAME%"
if exist "%CLI_NAME%.spec" del /q "%CLI_NAME%.spec"
echo.

echo [4/4] Running PyInstaller...
python -m PyInstaller --noconfirm --clean --onedir --console --name "%CLI_NAME%" cli.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo ==================================================
echo  Build OK: dist\%CLI_NAME%\%CLI_NAME%.exe
echo  Copy the whole dist\%CLI_NAME%\ folder, not just the .exe.
echo.
echo  Smoke test it before trusting a pipeline to it:
echo    dist\%CLI_NAME%\%CLI_NAME%.exe --version
echo    dist\%CLI_NAME%\%CLI_NAME%.exe list-hardware
echo    dist\%CLI_NAME%\%CLI_NAME%.exe flash tests\sample.hex --dry-run
echo ==================================================

endlocal
