set out [lindex $argv 0]
connect
jtag targets -set -filter {name =~ "*JTAG-SMT2*"}
puts "JTAG freq before: [jtag frequency]"
targets -set -nocase -filter {name =~ "*A53*#0"}
configparams force-mem-accesses 1
set bufs {first_block_2 0x10418000 1048576 add 0x11018000 1048576 final 0x15198000 1048576}
foreach {name addr words} $bufs {
    mrd -bin -file "$out/r1_$name.bin" $addr $words
    mrd -bin -file "$out/r2_$name.bin" $addr $words
    puts "READ $name"
}
jtag targets -set -filter {name =~ "*JTAG-SMT2*"}
jtag frequency 5000000
puts "JTAG freq slow: [jtag frequency]"
targets -set -nocase -filter {name =~ "*A53*#0"}
foreach {name addr words} $bufs {
    mrd -bin -file "$out/r3_$name.bin" $addr $words
    puts "READSLOW $name"
}
disconnect
