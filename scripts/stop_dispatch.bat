@echo off
title Dispatch - Stopping Video AI Engine
cd /d "C:\CODE\supoclip"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\CODE\supoclip\scripts\stop_dispatch.ps1"
