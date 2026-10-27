// clk_div.v
// Generates the two I2S timing signals (BCLK and WS) from the 100 MHz system clock.

module clk_div #(
    parameter HALF_PERIOD = 16,   
    parameter SLOT_BITS   = 32    
)(
    input  wire clk,        
    input  wire rst,        

    output reg  bclk,       
    output reg  ws,        

    output reg  bclk_rise,  
    output reg  bclk_fall,  
    output reg  ws_edge     
);

    
    reg [$clog2(HALF_PERIOD)-1:0] half_cnt; 
    reg [$clog2(SLOT_BITS)-1:0]   bit_cnt;    

    always @(posedge clk) begin
        bclk_rise <= 1'b0;
        bclk_fall <= 1'b0;
        ws_edge   <= 1'b0;

        if (rst) begin
            half_cnt <= 0;
            bit_cnt  <= 0;
            bclk     <= 1'b0;
            ws       <= 1'b0;
        end else if (half_cnt == HALF_PERIOD - 1) begin
            half_cnt <= 0;
            bclk     <= ~bclk;

            if (!bclk) begin
                bclk_rise <= 1'b1;
            end else begin
                bclk_fall <= 1'b1;
                if (bit_cnt == SLOT_BITS - 1) begin
                    bit_cnt <= 0;
                    ws      <= ~ws;
                    ws_edge <= 1'b1;
                end else begin
                    bit_cnt <= bit_cnt + 1;
                end
            end
        end else begin
            half_cnt <= half_cnt + 1;
        end
    end

endmodule
