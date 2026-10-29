`timescale 1ns/1ps
module tb_delay_finder;
    localparam N   = 1024;
    localparam LEN = 1024;                    // lines in each .mem file
    localparam CW  = $clog2(N+1);

    
    localparam signed [4:0] EXP12 = 13;      // ch1_ch2
    localparam signed [4:0] EXP13 = 5;      // ch1_ch3
    localparam signed [4:0] EXP23 = -8;      // ch2_ch3

    reg clk = 0, rst = 1, valid = 0;
    reg [15:0] ch1 [0:LEN-1];
    reg [15:0] ch2 [0:LEN-1];
    reg [15:0] ch3 [0:LEN-1];
    reg [15:0] s1 = 0, s2 = 0, s3 = 0;

    wire signed [4:0] lag12, lag13, lag23;
    wire [CW-1:0] m12, z12, p12, m13, z13, p13, m23, z23, p23;
    wire e12, e13, e23, d12, d13, d23;

    // pair (x, y): positive lag means y lags x
    delay_finder #(.N(N)) u12 (.clk(clk), .rst(rst), .valid(valid),
        .x(s1), .y(s2), .best_lag(lag12), .score_m1(m12), .score_0(z12),
        .score_p1(p12), .edge_flag(e12), .done(d12));
    delay_finder #(.N(N)) u13 (.clk(clk), .rst(rst), .valid(valid),
        .x(s1), .y(s3), .best_lag(lag13), .score_m1(m13), .score_0(z13),
        .score_p1(p13), .edge_flag(e13), .done(d13));
    delay_finder #(.N(N)) u23 (.clk(clk), .rst(rst), .valid(valid),
        .x(s2), .y(s3), .best_lag(lag23), .score_m1(m23), .score_0(z23),
        .score_p1(p23), .edge_flag(e23), .done(d23));

    always #5 clk = ~clk;                     // 100 MHz

    integer n, errors = 0, windows = 0;

    initial begin
        $readmemh("ch1.mem", ch1);
        $readmemh("ch2.mem", ch2);
        $readmemh("ch3.mem", ch3);
        $display("loaded: ch1[0]=%h ch1[%0d]=%h", ch1[0], LEN-1, ch1[LEN-1]);

        repeat (5) @(posedge clk);
        rst <= 0;

        for (n = 0; n < LEN; n = n + 1) begin
            @(posedge clk);
            s1 <= ch1[n]; s2 <= ch2[n]; s3 <= ch3[n];
            valid <= 1;
            @(posedge clk);
            valid <= 0;
            repeat (40) @(posedge clk);       // gap between samples
        end
        repeat (50) @(posedge clk);

        if (windows == 0)
            $display("FAIL: no window completed");
        else if (errors == 0)
            $display("PASS (%0d window(s) checked)", windows);
        else
            $display("FAIL: %0d mismatching window(s)", errors);
        $finish;
    end

    // all three modules finish together, so checking d12 is enough
    always @(posedge clk) if (d12) begin
        windows = windows + 1;
        $display("window %0d", windows);
        $display("  1-2: lag=%0d (exp %0d) scores %0d/%0d/%0d edge=%b",
                 lag12, EXP12, m12, z12, p12, e12);
        $display("  1-3: lag=%0d (exp %0d) scores %0d/%0d/%0d edge=%b",
                 lag13, EXP13, m13, z13, p13, e13);
        $display("  2-3: lag=%0d (exp %0d) scores %0d/%0d/%0d edge=%b",
                 lag23, EXP23, m23, z23, p23, e23);
        if (lag12 !== EXP12 || lag13 !== EXP13 || lag23 !== EXP23)
            errors = errors + 1;
    end
endmodule