@echo off
REM Open the verified board demo and send one sample frame automatically.
call "%~dp0run.bat" 192.168.1.254 5060 --image "%~dp0samples\im0002.ppm" --flags proc,overlay,mask,stats,logit,morph --autosend %*
