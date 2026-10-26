#!/usr/bin/env python3
"""Generates the stored samples that the mic emulators play.

Writes mic0.mem, mic1.mem, mic2.mem: one 24-bit sample per line, as 6 hex digits
(two's complement, so negative values look like FFxxxx).

The three channels carry the SAME sound, but mic1 is delayed by 2 samples and
mic2 by 5 samples. That imitates sound reaching the three mics at slightly
different times (the thing the direction-finding algorithm measures later).
"""
import math

N = 256                # samples in the table; the sound repeats after this many
FULL_SCALE = 2**23 - 1 # biggest positive 24-bit value
DELAYS = [0, 2, 5]     # delay of mic0, mic1, mic2, in samples

# Two sine waves added together. Whole numbers of cycles fit in N samples,
# so the sound joins up smoothly when the table wraps round.
def signal(n):
    return (0.6 * math.sin(2 * math.pi * 5 * n / N) +
            0.3 * math.sin(2 * math.pi * 17 * n / N))

for mic, delay in enumerate(DELAYS):
    with open(f"mic{mic}.mem", "w") as f:
        for n in range(N):
            value = round(signal(n - delay) * FULL_SCALE)  # delayed copy
            f.write(f"{value & 0xFFFFFF:06X}\n")           # two's complement, 24 bits
    print(f"wrote mic{mic}.mem  (delay {delay} samples)")
