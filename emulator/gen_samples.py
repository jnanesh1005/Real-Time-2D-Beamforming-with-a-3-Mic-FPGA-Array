#!/usr/bin/env python3
"""Generates the stored samples that the mic emulators play."""

import math

N = 256                  
FULL_SCALE = 2**23 - 1 # biggest positive 24-bit value
DELAYS = [0, 2, 5]     # delays given to each mic

def signal(n):
    return (0.6 * math.sin(2 * math.pi * 5 * n / N) +
            0.3 * math.sin(2 * math.pi * 17 * n / N))

for mic, delay in enumerate(DELAYS):
    with open(f"mic{mic}.mem", "w") as f:
        for n in range(N):
            value = round(signal(n - delay) * FULL_SCALE)  # delayed copy
            f.write(f"{value & 0xFFFFFF:06X}\n")           # two's complement, 24 bits
    print(f"wrote mic{mic}.mem  (delay {delay} samples)")
