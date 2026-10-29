`timescale 1ns/1ps

module tb_delay_finder;
    localparam N   = 1024;
    localparam LEN = 1024;                    // lines in each .mem file
    localparam CW  = $clog2(N+1);

    // Dynamic Expected Lags (declared as reg so they can be set at runtime)
    integer exp12_tmp, exp13_tmp, exp23_tmp;
    reg signed [4:0] EXP12 = 13;              // ch1_ch2
    reg signed [4:0] EXP13 = 5;               // ch1_ch3
    reg signed [4:0] EXP23 = -8;              // ch2_ch3

    // Folder and File Paths
    string test_dir = ".";
    string ch1_path, ch2_path, ch3_path;

    reg clk = 0, rst = 1, valid = 0;
    reg [15:0] ch1 [0:LEN-1];
    reg [15:0] ch2 [0:LEN-1];
    reg [15:0] ch3 [0:LEN-1];
    reg [15:0] s1 = 0, s2 = 0, s3 = 0;

    wire signed [4:0] lag12, lag13, lag23;
    
    // Updated score wires to signed [41:0] to fit 16-bit MAC accumulator scores (prevents truncation)
    wire signed [41:0] m12, z12, p12, m13, z13, p13, m23, z23, p23;
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
        // Read folder path from command line option +TEST_DIR=<folder_path>
        if (!$value$plusargs("TEST_DIR=%s", test_dir)) begin
            test_dir = ".";
        end

        // Construct dynamic memory paths
        ch1_path = {test_dir, "/ch1.mem"};
        ch2_path = {test_dir, "/ch2.mem"};
        ch3_path = {test_dir, "/ch3.mem"};

        // Read dynamic expected lags from +EXP12=<val> +EXP13=<val> +EXP23=<val>
        if ($value$plusargs("EXP12=%d", exp12_tmp)) EXP12 = exp12_tmp[4:0];
        if ($value$plusargs("EXP13=%d", exp13_tmp)) EXP13 = exp13_tmp[4:0];
        if ($value$plusargs("EXP23=%d", exp23_tmp)) EXP23 = exp23_tmp[4:0];

        // Load channel memory files dynamically
        $readmemh(ch1_path, ch1);
        $readmemh(ch2_path, ch2);
        $readmemh(ch3_path, ch3);
        $display("[INFO] Running folder: %s", test_dir);

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
            $display("[RESULT] FAIL: no window completed");
        else if (errors == 0)
            $display("[RESULT] PASS (%0d window(s) checked)", windows);
        else
            $display("[RESULT] FAIL: %0d mismatching window(s)", errors);
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