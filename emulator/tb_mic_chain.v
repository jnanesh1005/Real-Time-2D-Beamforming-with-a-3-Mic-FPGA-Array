`timescale 1ns/1ps
// Full chain test:  clk_div -> 3 x mic_emu -> i2s_rx

module tb_mic_chain;
    reg clk = 0, rst = 1;
    always #5 clk = ~clk;                       

    wire bclk, ws, bclk_rise, bclk_fall, ws_edge;
    clk_div #(.HALF_PERIOD(16), .SLOT_BITS(32)) div (
        .clk(clk), .rst(rst), .bclk(bclk), .ws(ws),
        .bclk_rise(bclk_rise), .bclk_fall(bclk_fall), .ws_edge(ws_edge));

    wire [2:0] sd;
    mic_emu #(.MEM_FILE("mic0.mem")) e0 (.clk(clk), .bclk(bclk), .ws(ws), .sd(sd[0]));
    mic_emu #(.MEM_FILE("mic1.mem")) e1 (.clk(clk), .bclk(bclk), .ws(ws), .sd(sd[1]));
    mic_emu #(.MEM_FILE("mic2.mem")) e2 (.clk(clk), .bclk(bclk), .ws(ws), .sd(sd[2]));

    wire [15:0] m0, m1, m2;
    wire valid;
    i2s_rx rx (.clk(clk), .rst(rst), .bclk_rise(bclk_rise), .ws_edge(ws_edge), .ws(ws),
               .sd(sd), .mic0(m0), .mic1(m1), .mic2(m2), .valid(valid));

    // expected data, read from the mem files
    localparam N = 256;
    reg [23:0] x0 [0:N-1];
    reg [23:0] x1 [0:N-1];
    reg [23:0] x2 [0:N-1];
    initial begin
        $readmemh("mic0.mem", x0);
        $readmemh("mic1.mem", x1);
        $readmemh("mic2.mem", x2);
    end

    integer checked = 0, errors = 0, idx = 0;
    integer last_valid_t = 0, bad_spacing = 0;
    always @(posedge clk) if (valid) begin
        idx = checked % N;                       // the table wraps round
        if (m0 !== x0[idx][23:8] || m1 !== x1[idx][23:8] || m2 !== x2[idx][23:8]) begin
            if (errors < 5)
                $display("MISMATCH sample %0d: got %h %h %h expected %h %h %h", checked,
                         m0, m1, m2, x0[idx][23:8], x1[idx][23:8], x2[idx][23:8]);
            errors = errors + 1;
        end
        // valid should come once per frame = 64 BCLK = 20480 ns
        if (checked > 0 && ($time - last_valid_t) != 20480) bad_spacing = bad_spacing + 1;
        last_valid_t = $time;
        checked = checked + 1;
    end

    initial begin
        #100 rst = 0;
        #(20480 * 300);                          // 300 frames: more than one trip round the table
        if (errors == 0 && bad_spacing == 0 && checked >= 295)
            $display("PASS: %0d samples x 3 mics matched (table wrapped %0d time(s)), valid once per frame",
                     checked, checked / N);
        else
            $display("FAIL: %0d errors, %0d bad valid spacing, %0d samples checked",
                     errors, bad_spacing, checked);
        $finish;
    end
endmodule
