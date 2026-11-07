@echo off
rem Windows: double-click this file to start EF Studios Artist Finder.
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if exist ".venv\Scripts\python.exe" goto install
echo First run: setting up Artist Finder (about a minute)...
%PY% -m venv .venv
if errorlevel 1 goto nopython
:install
if exist ".venv\.installed" goto run
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -e .
if errorlevel 1 goto failed
echo done> ".venv\.installed"
:run
if not exist ".env" copy ".env.example" ".env" >nul
echo Starting Artist Finder at http://127.0.0.1:5005  (close this window to stop)
".venv\Scripts\artist-email-finder.exe" serve %*
pause
goto :eof
:nopython
echo Artist Finder needs Python 3.10 or newer.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
pause
goto :eof
:failed
echo Setup failed. Check your internet connection and run this again.
pause
