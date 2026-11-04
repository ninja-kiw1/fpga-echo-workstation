@echo off
rem run_sim.bat [plusargs...]  -- xsim: rtl_lb (new) vs rtl (old, renamed *_old), byte-exact
setlocal
cd /d "%~dp0"
set "XB=E:\AMD\Xilinx\2025.2\Vivado\bin"
call "%XB%\xvlog.bat" --sv ..\img_proc_axil.v ..\img_proc_axis.v ..\mode_map.v ..\pcolor_lut.v ..\recip_lut.v old\img_proc_axil_old.v old\img_proc_axis_old.v old\gray_frame_store_old.v old\mode_map_old.v old\pcolor_lut_old.v old\recip_lut_old.v tb_equiv.sv > _xvlog.out 2>&1 || (type _xvlog.out & exit /b 1)
call "%XB%\xelab.bat" -O3 --debug off tb_equiv -s tb_equiv_snap > _xelab.out 2>&1 || (type _xelab.out & exit /b 2)
call "%XB%\xsim.bat" tb_equiv_snap -R %* > _xsim.out 2>&1
findstr /C:"PASS" /C:"FAIL" /C:"SUMMARY" /C:"FATAL" /C:"ERROR" _xsim.out
