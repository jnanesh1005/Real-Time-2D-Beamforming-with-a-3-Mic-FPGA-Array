`timescale 1ns/1ps

module delay_finder #(
    parameter WIDTH  = 16, // No. of bits in Input samples
    parameter N = 1024,    // window length in samples
    parameter MAXLAG = 13  // lags -MAXLAG .. +MAXLAG
)(
    input  wire clk,        
    input  wire rst,       // synchronous, active high
    input  wire valid,     // when high => new input arrived 
    input  wire signed [WIDTH-1:0] x, // to store 16(WIDTH) bit signed input from each of two mic 
    input  wire signed [WIDTH-1:0] y,

    output reg signed [$clog2(MAXLAG+1):0] best_lag,   // -13..+13 (5 bit)
    output reg signed [41:0] score_m1,   // score at best-1 (42-bit signed accumulator output)
    output reg signed [41:0] score_0,    // score at best
    output reg signed [41:0] score_p1,   // score at best+1
    output reg edge_flag,  // |best_lag| == MAXLAG
    output reg done        // 1 clk pulse: outputs fresh
);
    localparam MAXLAG_2S = 2*MAXLAG + 1; // 27
    localparam CW = $clog2(N+1);         // counter width (11)
    localparam IW = $clog2(MAXLAG_2S);   // lag-index width (5)
    localparam ACC_W = 42;               // 42-bit accumulator width (prevents overflow for 1024 x 16-bit products)

    // FSM states
    localparam S_ACC  = 2'd0,    // accumulating samples
               S_SCAN = 2'd1,    // scanning accumulators for the maximum
               S_OUT  = 2'd2;    // latch outputs, clear for next window

    // history: x_sr[d] = x delayed d samples, y_sr[d] = y delayed d samples
    reg signed [WIDTH-1:0] x_sr [0:MAXLAG_2S-1]; // stores last 27 input 16-bit x samples
    reg signed [WIDTH-1:0] y_sr [0:MAXLAG];      // stores last 14 input 16-bit y samples

    // one agreement accumulator per lag; index i <-> lag k = MAXLAG - i
    // each acc[i] defined with size of ACC_W(42) bits
    reg signed [ACC_W-1:0] acc [0:MAXLAG_2S-1];

    //Keeps a track if 1024 samples window was comppleted and then increments to next FSM state
    reg [CW-1:0] count;                 
    
    //Indeicates Current FSM state
    reg [1:0] state;

    //interating variable thorugh acc to find max
    reg [IW-1:0] scan_i;              

    //stores the best max acc
    reg signed [ACC_W-1:0] best_val;            
    //stores the index of best_val
    reg [IW-1:0] best_idx;           

    integer i;

    always @(posedge clk) begin
        done <= 1'b0;                              // default: pulse only

        if (rst) begin
            state     <= S_ACC;
            count     <= 0;
            scan_i    <= 0;
            best_val  <= 0;
            best_idx  <= 0;
            best_lag  <= 0;
            score_m1  <= 0;
            score_0   <= 0;
            score_p1  <= 0;
            edge_flag <= 1'b0;
            for (i = 0; i < MAXLAG_2S; i = i + 1) x_sr[i] <= 0;
            for (i = 0; i <= MAXLAG; i = i + 1) y_sr[i] <= 0;
            for (i = 0; i < MAXLAG_2S; i = i + 1) acc[i] <= 0;
        end
        else begin
            //When valid is high x,y are updated into 16-bit sample history shift registers
            if (valid) begin
                for (i = MAXLAG_2S-1; i > 0; i = i - 1)
                    x_sr[i] <= x_sr[i-1];
                x_sr[0] <= x;

                for (i = MAXLAG; i > 0; i = i - 1)
                    y_sr[i] <= y_sr[i-1];
                y_sr[0] <= y;
            end
            //FSM Begins
            case (state)
                // Accumulating samples
                S_ACC: begin
                    if (valid) begin
                        //i represents delay index, multiplied with y_sr delayed sample at MAXLAG(13)
                        for (i = 0; i < MAXLAG_2S; i = i + 1)
                            acc[i] <= acc[i] + (x_sr[i] * y_sr[MAXLAG]); // 16-bit signed multiplication and accumulation

                        if (count == N-1) begin              // if N samples were accounted , proceed to next state of fsm
                            count  <= 0;
                            scan_i <= 0;
                            state  <= S_SCAN;
                        end
                        else
                            count <= count + 1'b1;          
                    end
                end

                // Scanning through acc to find max
                S_SCAN: begin
                    // strict '>' : ties keep the LOWEST index (most negative lag)
                    if (scan_i == 0 || acc[scan_i] > best_val) begin
                        best_val <= acc[scan_i];
                        best_idx <= scan_i;
                    end

                    if (scan_i == MAXLAG_2S-1)  //once all of acc is scanned proceed to next state 
                        state <= S_OUT;
                    else
                        scan_i <= scan_i + 1'b1;
                end

                //Output
                S_OUT: begin
                    // Corrected sign mapping (lag = MAXLAG - index)
                    best_lag  <= $signed({1'b0, best_idx}) - MAXLAG;
                    score_0   <= acc[best_idx];
                    score_m1  <= (best_idx == 0) ? {ACC_W{1'b0}} : acc[best_idx - 1'b1];
                    score_p1  <= (best_idx == MAXLAG_2S-1) ? {ACC_W{1'b0}} : acc[best_idx + 1'b1];
                    edge_flag <= (best_idx == 0) || (best_idx == MAXLAG_2S-1);
                    done      <= 1'b1;
                    //resetting shift registers and accumulators for next window
                    for (i = 0; i < MAXLAG_2S; i = i + 1) x_sr[i] <= 0;
                    for (i = 0; i <= MAXLAG; i = i + 1) y_sr[i] <= 0;
                    for (i = 0; i < MAXLAG_2S; i = i + 1) acc[i] <= 0;
                    state <= S_ACC;
                end

                default: state <= S_ACC;
            endcase
        end
    end
endmodule