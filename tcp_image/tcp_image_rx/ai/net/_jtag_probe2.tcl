# _jtag_probe2.tcl -- 再来一次，给 hw_server 足够的扫描时间
set ok 0
if {[catch {connect} e]} {
    puts "CONNECT_FAIL: $e"
} else {
    puts "JTAG_CONNECT_OK"
    after 6000
    for {set i 0} {$i < 5} {incr i} {
        if {[catch {set t [targets]} te]} {
            puts "try$i TARGETS_FAIL: $te"
        } else {
            puts "try$i TARGETS: $t"
            if {[llength $t] > 0} { set ok 1; break }
        }
        after 3000
    }
}
puts "PROBE_RESULT ok=$ok"
exit 0
