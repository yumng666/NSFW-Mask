@echo off
rem NSFW Mask - installer launcher (double-click friendly)
rem -ExecutionPolicy Bypass: zipped .ps1 files are blocked by default policy
rem The .ps1 pauses on its own for every outcome, so errors stay visible.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"
