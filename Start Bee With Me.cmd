@echo off
rem One-click start for Windows: double-click this file (or a shortcut to it).
rem Runs start.ps1 from the folder this file is in. On an error the window stays open so the
rem message can be read or photographed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
