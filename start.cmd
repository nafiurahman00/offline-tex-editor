@echo off
rem Start the editor from a source checkout (no installation needed).
rem Usage: start.cmd [--root PROJECT_DIR] [--main FILE] [--engine auto^|pdflatex^|xelatex^|lualatex] [--open] ...
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
where py >nul 2>nul && (py -3 -m offline_tex_editor %*) || (python -m offline_tex_editor %*)
