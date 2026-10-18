@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===========================================================================
echo   🌈 Bambu Studio (.3MF / .STL) -^> SolidWorks Web Studyosu Baslatiliyor...
echo   🌐 Tarayicida otomatik acilacak: http://127.0.0.1:5050
echo ===========================================================================
".\.venv\Scripts\python.exe" web_app.py
pause
