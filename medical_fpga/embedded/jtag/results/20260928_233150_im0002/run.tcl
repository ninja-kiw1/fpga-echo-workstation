
# The JTAG link shows occasional transfer errors (DAP ACK / corrupted words),
# so every access is retried and every transfer is verified.
proc step {msg} { puts "STEP $msg"; flush stdout }
proc retry {script {tries 5}} {
    for {set i 1} {$i <= $tries} {incr i} {
        if {![catch {uplevel 1 $script} result]} { return $result }
        puts "JTAGRETRY $i: $result"; flush stdout
        after 200
        # A dropped debug context is recovered by selecting the memory target again.
        catch {targets -set -nocase -filter {name =~ "*APU*"}}
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

set ::workdir {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002}
connect
jtag targets -set -filter {level == 0}
jtag frequency 10000000
step "connected: [lindex [split [jtag targets] \n] 0], JTAG [jtag frequency] Hz"
targets -set -nocase -filter {name =~ "*PSU*"}
rst -system
after 3000
targets -set -nocase -filter {name =~ "PL"}
fpga -file {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/xsa/unet_system.bit}
step "bitstream programmed"
targets -set -nocase -filter {name =~ "*APU*"}
source {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/xsa/psu_init.tcl}
psu_init
after 1000
psu_ps_pl_isolation_removal
after 1000
psu_ps_pl_reset_config
catch {psu_protection}
step "psu_init done (clocks, DDR, PS-PL isolation removed)"
# Memory and register accesses go through the APU memory access port (as psu_init
# does); the A53 cores stay in reset. Going through core #0's debug interface
# dropped the debug context ("EDITR not ready") during long read-backs.
targets -set -nocase -filter {name =~ "*APU*"}
configparams force-mem-accesses 1

# DDR sanity check at both ends of the region used.
foreach a {0x10000000 0x15597ffc} {
    wreg $a 0x5A5AA5A5
}
step "DDR read/write ok"
foreach {name base} {conv_layer_0 0x80000000 eltwise_layer_0 0x80010000} {
    set ctrl [rd $base]
    puts "IP $name ctrl=[format 0x%x $ctrl]"
    if {($ctrl & 4) == 0} { error "$name not idle" }
}
step "both IPs idle"

timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/first_block_2.bin} 0x10000000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/first_block_5.bin} 0x10001000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/first_shortcut_1.bin} 0x10002000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/down_path_0_block_2.bin} 0x10003000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/down_path_0_block_5.bin} 0x10004000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/down_path_0_shortcut_1.bin} 0x10005000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/down_path_1_block_2.bin} 0x10006000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/down_path_1_block_5.bin} 0x10008000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/down_path_1_shortcut_1.bin} 0x1000b000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_0_up_layer_block_0.bin} 0x1000c000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_0_conv_bridge_layer_block_2.bin} 0x1000d000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_0_conv_layer_block_2.bin} 0x1000e000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_0_conv_layer_block_5.bin} 0x10010000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_0_conv_layer_shortcut_1.bin} 0x10011000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_1_up_layer_block_0.bin} 0x10012000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_1_conv_bridge_layer_block_2.bin} 0x10013000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_1_conv_layer_block_2.bin} 0x10014000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_1_conv_layer_block_5.bin} 0x10015000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/up_path_1_conv_layer_shortcut_1.bin} 0x10016000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/params/final.bin} 0x10017000
timed_dow {E:/PLD/medical_fpga/deployment/unet_network/vectors/images/im0002/input.bin} 0x10018000
step "parameters and input loaded"
set t_net [clock microseconds]
wreg 0x80000018 0x10018000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x10418000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10000000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000003
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001b
wreg 0x80000074 0xffff8001
wreg 0x8000007c 0x00007fff
run_ip 0x80000000 first_block_2
wreg 0x80000018 0x10418000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x10818000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10001000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000008
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 first_block_5
wreg 0x80000018 0x10018000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x10c18000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10002000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000001
wreg 0x80000044 0x00000003
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 first_shortcut_1
wreg 0x80010018 0x10818000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x10c18000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x11018000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000000
wreg 0x80010044 0x00000008
wreg 0x8001004c 0x00000008
wreg 0x80010054 0x00000200
wreg 0x8001005c 0x00000200
wreg 0x80010064 0x3ef9285d
wreg 0x8001006c 0x1a0e1828
wreg 0x80010074 0x0000001e
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 add
wreg 0x80010018 0x11018000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x11018000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x11418000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000002
wreg 0x80010044 0x00000008
wreg 0x8001004c 0x00000000
wreg 0x80010054 0x00000200
wreg 0x8001005c 0x00000200
wreg 0x80010064 0x00000000
wreg 0x8001006c 0x00000000
wreg 0x80010074 0x00000000
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 down_path_0_pool
wreg 0x80000018 0x11418000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x11518000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10003000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000008
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 down_path_0_block_2
wreg 0x80000018 0x11518000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x11718000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10004000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 down_path_0_block_5
wreg 0x80000018 0x11418000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x11918000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10005000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000001
wreg 0x80000044 0x00000008
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 down_path_0_shortcut_1
wreg 0x80010018 0x11718000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x11918000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x11b18000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000000
wreg 0x80010044 0x00000010
wreg 0x8001004c 0x00000010
wreg 0x80010054 0x00000100
wreg 0x8001005c 0x00000100
wreg 0x80010064 0x3c465fc1
wreg 0x8001006c 0x1ec40a7c
wreg 0x80010074 0x0000001e
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 add_1
wreg 0x80010018 0x11b18000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x11b18000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x11d18000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000002
wreg 0x80010044 0x00000010
wreg 0x8001004c 0x00000000
wreg 0x80010054 0x00000100
wreg 0x8001005c 0x00000100
wreg 0x80010064 0x00000000
wreg 0x8001006c 0x00000000
wreg 0x80010074 0x00000000
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 down_path_1_pool
wreg 0x80000018 0x11d18000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x11d98000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10006000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000020
wreg 0x80000054 0x00000080
wreg 0x8000005c 0x00000080
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 down_path_1_block_2
wreg 0x80000018 0x11d98000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x11e98000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10008000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000020
wreg 0x8000004c 0x00000020
wreg 0x80000054 0x00000080
wreg 0x8000005c 0x00000080
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 down_path_1_block_5
wreg 0x80000018 0x11d18000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x11f98000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x1000b000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000001
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000020
wreg 0x80000054 0x00000080
wreg 0x8000005c 0x00000080
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 down_path_1_shortcut_1
wreg 0x80010018 0x11e98000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x11f98000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x12098000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000000
wreg 0x80010044 0x00000020
wreg 0x8001004c 0x00000020
wreg 0x80010054 0x00000080
wreg 0x8001005c 0x00000080
wreg 0x80010064 0x438d138f
wreg 0x8001006c 0x10e3889a
wreg 0x80010074 0x0000001e
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 add_2
wreg 0x80000018 0x12098000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x12198000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x1000c000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000002
wreg 0x80000044 0x00000020
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000080
wreg 0x8000005c 0x00000080
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_0_up_layer_block_0
wreg 0x80000018 0x11b18000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x12398000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x1000d000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_0_conv_bridge_layer_block_2
wreg 0x80010018 0x12198000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x12398000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x12598000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000001
wreg 0x80010044 0x00000010
wreg 0x8001004c 0x00000010
wreg 0x80010054 0x00000100
wreg 0x8001005c 0x00000100
wreg 0x80010064 0x2bf15739
wreg 0x8001006c 0x40000000
wreg 0x80010074 0x0000001e
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 cat
wreg 0x80000018 0x12598000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x12998000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x1000e000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000020
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffff8001
wreg 0x8000007c 0x00007fff
run_ip 0x80000000 up_path_0_conv_layer_block_2
wreg 0x80000018 0x12998000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x12b98000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10010000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_0_conv_layer_block_5
wreg 0x80000018 0x12598000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x12d98000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10011000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000001
wreg 0x80000044 0x00000020
wreg 0x8000004c 0x00000010
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_0_conv_layer_shortcut_1
wreg 0x80010018 0x12b98000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x12d98000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x12f98000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000000
wreg 0x80010044 0x00000010
wreg 0x8001004c 0x00000010
wreg 0x80010054 0x00000100
wreg 0x8001005c 0x00000100
wreg 0x80010064 0x3e277e5f
wreg 0x8001006c 0x14ea24f3
wreg 0x80010074 0x0000001e
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 add_3
wreg 0x80000018 0x12f98000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x13198000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10012000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000002
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000100
wreg 0x8000005c 0x00000100
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_1_up_layer_block_0
wreg 0x80000018 0x11018000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x13598000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10013000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000008
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_1_conv_bridge_layer_block_2
wreg 0x80010018 0x13198000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x13598000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x13998000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000001
wreg 0x80010044 0x00000008
wreg 0x8001004c 0x00000008
wreg 0x80010054 0x00000200
wreg 0x8001005c 0x00000200
wreg 0x80010064 0x3f0e7464
wreg 0x8001006c 0x40000000
wreg 0x80010074 0x0000001e
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 cat_1
wreg 0x80000018 0x13998000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x14198000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10014000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_1_conv_layer_block_2
wreg 0x80000018 0x14198000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x14598000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10015000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000000
wreg 0x80000044 0x00000008
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000001
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_1_conv_layer_block_5
wreg 0x80000018 0x13998000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x14998000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10016000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000001
wreg 0x80000044 0x00000010
wreg 0x8000004c 0x00000008
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 up_path_1_conv_layer_shortcut_1
wreg 0x80010018 0x14598000
wreg 0x8001001c 0x00000000
wreg 0x80010024 0x14998000
wreg 0x80010028 0x00000000
wreg 0x80010030 0x14d98000
wreg 0x80010034 0x00000000
wreg 0x8001003c 0x00000000
wreg 0x80010044 0x00000008
wreg 0x8001004c 0x00000008
wreg 0x80010054 0x00000200
wreg 0x8001005c 0x00000200
wreg 0x80010064 0x406edfa0
wreg 0x8001006c 0x3c8a85cd
wreg 0x80010074 0x0000001e
wreg 0x8001007c 0xffffff81
wreg 0x80010084 0x0000007f
run_ip 0x80010000 add_4
wreg 0x80000018 0x14d98000
wreg 0x8000001c 0x00000000
wreg 0x80000024 0x15198000
wreg 0x80000028 0x00000000
wreg 0x80000030 0x10017000
wreg 0x80000034 0x00000000
wreg 0x8000003c 0x00000001
wreg 0x80000044 0x00000008
wreg 0x8000004c 0x00000001
wreg 0x80000054 0x00000200
wreg 0x8000005c 0x00000200
wreg 0x80000064 0x00000000
wreg 0x8000006c 0x0000001e
wreg 0x80000074 0xffffff81
wreg 0x8000007c 0x0000007f
run_ip 0x80000000 final
puts "NETWORK_US [expr {[clock microseconds] - $t_net}]"
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/first_block_2.bin} 0x10418000 1048576
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/add.bin} 0x11018000 1048576
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/down_path_0_pool.bin} 0x11418000 262144
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/add_2.bin} 0x12098000 262144
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/up_path_0_up_layer_block_0.bin} 0x12198000 524288
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/cat.bin} 0x12598000 1048576
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/up_path_0_conv_layer_block_2.bin} 0x12998000 524288
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/add_4.bin} 0x14d98000 1048576
timed_dump {E:/PLD/medical_fpga/embedded/jtag/results/20260928_233150_im0002/final.bin} 0x15198000 1048576
step "finished"
disconnect
