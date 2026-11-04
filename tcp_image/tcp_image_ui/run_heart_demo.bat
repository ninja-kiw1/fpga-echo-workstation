@echo off
REM Acceptance demo: echo workstation maximized, three modes pre-loaded
REM (snapshot = CAMUS patient0027 4CH, keyframe = HMC-QU ES000108, live = 50 fps
REM EchoNet test source with the fixed crop), ends in live mode.
REM   run_heart_demo.bat               board at 192.168.1.254:5060
REM   run_heart_demo.bat 127.0.0.1     on the board itself (DP monitor)
call "%~dp0run_heart.bat" %* --demo
