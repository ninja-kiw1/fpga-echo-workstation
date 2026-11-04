# Open the deployed f150 project read-only and show its block design (demo only).
set here [file dirname [file normalize [info script]]]
open_project -read_only [file join $here ai_sys_f150 ai_sys_f150.xpr]
open_bd_design [get_files system.bd]
start_gui
