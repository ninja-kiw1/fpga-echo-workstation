
# The JTAG link shows occasional transfer errors (DAP ACK / corrupted words),
# so every access is retried and every transfer is verified.
proc step {msg} { puts "STEP $msg"; flush stdout }
proc retry {script {tries 5}} {
    for {set i 1} {$i <= $tries} {incr i} {
        if {![catch {uplevel 1 $script} result]} { return $result }
        puts "JTAGRETRY $i: $result"; flush stdout
        after [expr {500 * $i}]
        # A corrupted scan can drop the debug context ("Invalid context"):
        # reconnect and select the memory target again.
        catch {disconnect}
        catch {open_link}
    }
    error "JTAG access failed after $tries tries: $script"
}
proc rd {addr} { return [retry [list mrd -value $addr]] }
proc wreg {addr value} {
    # Write, read back, compare; the AXI-Lite argument registers are readable.
    for {set i 0} {$i < 5} {incr i} {
        retry [list mwr $addr $value]
        if {[rd $addr] == $value} { return }
    }
    error "register write failed at $addr"
}
proc read_bytes {path} { set f [open $path rb]; set d [read $f]; close $f; return $d }
proc run_ip {base name} {
    retry [list mwr $base 1]
    set t0 [clock microseconds]
    while {([rd $base] & 2) == 0} {
        if {[clock microseconds] - $t0 > 30000000} { error "TIMEOUT $name ctrl=[format 0x%x [rd $base]]" }
    }
    set us [expr {[clock microseconds] - $t0}]
    set ret [rd [expr {$base + 0x10}]]
    puts "NODE $name $us $ret"; flush stdout
    if {$ret != 0} { error "BADRETURN $name $ret" }
}
set ::chunk_words 16384   ;# 64 KiB per verified read
proc stable_read {addr words} {
    # Read in 64 KiB chunks; a chunk is accepted when two consecutive reads agree.
    # Transfer errors are sparse and random, so a small chunk quickly reads clean twice.
    set tmp [file join $::workdir chunk.tmp]
    set data ""
    set rereads 0
    for {set done 0} {$done < $words} {incr done $::chunk_words} {
        set n [expr {min($::chunk_words, $words - $done)}]
        set a [expr {$addr + 4 * $done}]
        retry [list mrd -bin -file $tmp $a $n]
        set prev [read_bytes $tmp]
        set ok 0
        for {set i 0} {$i < 10} {incr i} {
            retry [list mrd -bin -file $tmp $a $n]
            set cur [read_bytes $tmp]
            if {$cur eq $prev} { set ok 1; break }
            incr rereads
            set prev $cur
        }
        if {!$ok} { error "chunk at [format 0x%x $a] never read the same twice" }
        append data $cur
    }
    file delete $tmp
    if {$rereads} { puts "DUMPRETRY $rereads chunk re-reads at [format 0x%x $addr]"; flush stdout }
    return $data
}
proc timed_dow {file addr} {
    # Upload, then verify with stable reads; re-upload on a real mismatch.
    set want [read_bytes $file]
    set words [expr {([string length $want] + 3) / 4}]
    set t0 [clock milliseconds]
    for {set i 1} {$i <= 4} {incr i} {
        retry [list dow -data $file $addr]
        set got [string range [stable_read $addr $words] 0 [expr {[string length $want] - 1}]]
        if {$got eq $want} {
            puts "LOAD [file tail $file] [file size $file] [expr {[clock milliseconds] - $t0}] attempts=$i"; flush stdout
            return
        }
        puts "LOADRETRY [file tail $file] attempt $i"; flush stdout
    }
    error "upload of $file failed verification"
}
proc timed_dump {file addr words} {
    set t0 [clock milliseconds]
    set data [stable_read $addr $words]
    set f [open $file wb]; puts -nonewline $f $data; close $f
    puts "DUMP [file tail $file] [expr {$words * 4}] [expr {[clock milliseconds] - $t0}]"; flush stdout
}

set ::workdir {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233544_im0002}
set ::jtag_hz 5000000
proc open_link {} {
    connect
    jtag targets -set -filter {level == 0}
    jtag frequency $::jtag_hz
    # Memory and register accesses go through the APU memory access port (as
    # psu_init does); the A53 cores stay in reset. Going through core #0's debug
    # interface dropped the debug context ("EDITR not ready") in long read-backs.
    targets -set -nocase -filter {name =~ "*APU*"}
    configparams force-mem-accesses 1
}
open_link
step "connected: [lindex [split [jtag targets] \n] 0], JTAG [jtag frequency] Hz"

step "read-back only (no reset, no reprogramming)"
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233544_im0002/add_4.bin} 0x14d98000 1048576
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233544_im0002/final.bin} 0x15198000 1048576
step "finished"
disconnect
