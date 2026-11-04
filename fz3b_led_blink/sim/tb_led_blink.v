`timescale 1ns / 1ps
module tb_led_blink;
    reg clk = 0;
    wire led, led_fast;
    integer cycle;
    led_blink #(.HALF_PERIOD_CYCLES(5)) dut(clk, led);
    led_blink #(.HALF_PERIOD_CYCLES(1)) fast_dut(clk, led_fast);
    always #20 clk = ~clk;
    initial begin
        #1;
        if (led !== 0 || led_fast !== 0) $fatal(1, "Initial state failed");
        for (cycle = 1; cycle <= 40; cycle = cycle + 1) begin
            @(posedge clk);
            #1;
            if (led !== ((cycle / 5) % 2))
                $fatal(1, "Incorrect LED timing at cycle %0d", cycle);
            if (led_fast !== (cycle % 2))
                $fatal(1, "Single-cycle period failed at cycle %0d", cycle);
        end
        $display("PASS: initial state, eight half-periods, and minimum period");
        $finish;
    end
endmodule
