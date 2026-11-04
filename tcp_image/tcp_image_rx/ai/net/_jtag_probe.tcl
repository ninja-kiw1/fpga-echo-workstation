# _jtag_probe.tcl -- 看看 JTAG 上能不能看到板子
if {[catch {connect} e]} {
    puts "CONNECT_FAIL: $e"
    exit 1
}
puts "JTAG_CONNECT_OK"
if {[catch {targets} t]} {
    puts "TARGETS_FAIL: $t"
} else {
    puts "TARGETS: $t"
}
exit 0
