@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 server.py & goto :eof)
where python >nul 2>nul && (python server.py & goto :eof)
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" ("%LOCALAPPDATA%\Programs\Python\Python314\python.exe" server.py & goto :eof)
echo Python 3 was not found. Install it from python.org, then run this again.
pause
