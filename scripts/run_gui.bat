@echo off
rem Development launcher: run the GUI straight from the source tree.
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
python "%ROOT%\scripts\run_gui.py" %*
