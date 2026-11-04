# FZ3B hardware manual sections 18 and 19; community pin map.
# https://ai.baidu.com/ai-doc/HWCE/nkq9b5ncu
set_property PACKAGE_PIN D4 [get_ports pl_ref_clk]
set_property IOSTANDARD LVCMOS18 [get_ports pl_ref_clk]
create_clock -name pl_ref_clk -period 40.000 [get_ports pl_ref_clk]

set_property PACKAGE_PIN A10 [get_ports pl_led]
set_property IOSTANDARD LVCMOS33 [get_ports pl_led]
set_property DRIVE 4 [get_ports pl_led]
set_property SLEW SLOW [get_ports pl_led]
# Human-visible indicator, no receiving synchronous device.
set_false_path -to [get_ports pl_led]
