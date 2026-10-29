#!/usr/bin/env python3
import os
import json
import subprocess
import re
import csv
import sys
from collections import defaultdict

# Dual-output logger (Console + Text File)
class TeeLogger:
    def __init__(self, filepath="terminal_output.txt"):
        self.terminal = sys.stdout
        self.log = open(filepath, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        self.log.close()

# Redirect stdout to write to both terminal and file
sys.stdout = TeeLogger("terminal_output.txt")

# Tools configuration matching your exact command
COMPILER = "iverilog"
SIMULATOR = "vvp"
SIM_BIN = "sim"
VERILOG_SOURCES = ["tb_delay_finder_16.v", "delay_finder_16.v"]

def compile_verilog():
    """Compiles the HDL design once."""
    print(f"Compiling: {COMPILER} -g2012 -o {SIM_BIN} {' '.join(VERILOG_SOURCES)}")
    cmd = [COMPILER, "-g2012", "-o", SIM_BIN] + VERILOG_SOURCES
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("\n--- COMPILATION ERROR ---")
        print(res.stderr)
        return False
    print("Compilation successful.\n")
    return True

def find_test_folders(root_dir="."):
    """Finds all test case subfolders containing .mem files and expected.json."""
    valid_folders = []
    for entry in os.scandir(root_dir):
        if entry.is_dir():
            has_mems = all(os.path.exists(os.path.join(entry.path, f"ch{i}.mem")) for i in (1, 2, 3))
            has_json = os.path.exists(os.path.join(entry.path, "expected.json"))
            if has_mems and has_json:
                valid_folders.append(entry.name)
    return sorted(valid_folders)

def parse_folder_metadata(folder_name):
    """Extracts rt60 and snr metadata from the folder name string."""
    match = re.search(r"rt60_([\d\.]+)_snr_(\d+)_angle_(\d+)_w(\d+)", folder_name)
    if match:
        return {
            "rt60": match.group(1),
            "snr": match.group(2),
            "angle": match.group(3),
            "window": match.group(4)
        }
    return {"rt60": "unknown", "snr": "unknown", "angle": "unknown", "window": "unknown"}

def run_test(folder_name):
    """Runs a single test case through vvp sim using command-line +args."""
    json_path = os.path.join(folder_name, "expected.json")
    with open(json_path, "r") as f:
        exp_data = json.load(f)

    exp12 = exp_data.get("ch1_ch2", 0)
    exp13 = exp_data.get("ch1_ch3", 0)
    exp23 = exp_data.get("ch2_ch3", 0)

    cmd = [
        SIMULATOR,
        SIM_BIN,
        f"+TEST_DIR={folder_name}",
        f"+EXP12={exp12}",
        f"+EXP13={exp13}",
        f"+EXP23={exp23}"
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    output = res.stdout

    # Extract measured lags using regex from testbench stdout
    m12 = re.search(r"1-2:\s+lag=(-?\d+)", output)
    m13 = re.search(r"1-3:\s+lag=(-?\d+)", output)
    m23 = re.search(r"2-3:\s+lag=(-?\d+)", output)

    meas12 = int(m12.group(1)) if m12 else None
    meas13 = int(m13.group(1)) if m13 else None
    meas23 = int(m23.group(1)) if m23 else None

    is_pass = "[RESULT] PASS" in output

    return is_pass, (exp12, exp13, exp23), (meas12, meas13, meas23)

def main():
    if not compile_verilog():
        return

    folders = find_test_folders(".")
    if not folders:
        print("No test folders with .mem and expected.json files found.")
        return

    print(f"Found {len(folders)} test cases. Starting simulation run...\n")
    print("=" * 83)
    print(f"{'Folder Name':<35} | {'Expected (12,13,23)':<19} | {'Measured (12,13,23)':<19} | {'Result':<6}")
    print("=" * 83)

    rt60_stats = defaultdict(lambda: {"pass": 0, "total": 0})
    snr_stats = defaultdict(lambda: {"pass": 0, "total": 0})
    results_csv_rows = []

    passed_count = 0

    for folder in folders:
        meta = parse_folder_metadata(folder)
        is_pass, exp_lags, meas_lags = run_test(folder)

        status_str = "PASS" if is_pass else "FAIL"
        if is_pass:
            passed_count += 1

        rt60_stats[meta["rt60"]]["total"] += 1
        snr_stats[meta["snr"]]["total"] += 1
        if is_pass:
            rt60_stats[meta["rt60"]]["pass"] += 1
            snr_stats[meta["snr"]]["pass"] += 1

        print(f"{folder:<35} | {str(exp_lags):<19} | {str(meas_lags):<19} | {status_str:<6}")

        results_csv_rows.append({
            "folder": folder,
            "rt60": meta["rt60"],
            "snr": meta["snr"],
            "angle": meta["angle"],
            "exp_ch1_ch2": exp_lags[0],
            "exp_ch1_ch3": exp_lags[1],
            "exp_ch2_ch3": exp_lags[2],
            "meas_ch1_ch2": meas_lags[0],
            "meas_ch1_ch3": meas_lags[1],
            "meas_ch2_ch3": meas_lags[2],
            "result": status_str
        })

    total_cases = len(folders)
    accuracy = (passed_count / total_cases) * 100

    print("=" * 83)
    print("SUMMARY STATS")
    print(f"Total Cases Tested : {total_cases}")
    print(f"Total Passed        : {passed_count}")
    print(f"Total Failed        : {total_cases - passed_count}")
    print(f"Overall Accuracy    : {accuracy:.2f}%\n")

    print("ACCURACY BY RT60 (Echo Level):")
    for r in sorted(rt60_stats.keys()):
        p = rt60_stats[r]["pass"]
        t = rt60_stats[r]["total"]
        acc = (p / t) * 100 if t > 0 else 0
        print(f"  RT60 = {r:<5}s : {p}/{t} ({acc:.2f}%)")

    print("\nACCURACY BY SNR (Noise Level):")
    for s in sorted(snr_stats.keys(), key=lambda x: int(x) if x.isdigit() else 0, reverse=True):
        p = snr_stats[s]["pass"]
        t = snr_stats[s]["total"]
        acc = (p / t) * 100 if t > 0 else 0
        print(f"  SNR = {s:<2} dB  : {p}/{t} ({acc:.2f}%)")
    print("=" * 83)

    # Save detailed CSV log
    with open("results.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results_csv_rows[0].keys())
        writer.writeheader()
        writer.writerows(results_csv_rows)
    print("Results exported to results.csv")
    print("Full output log written to terminal_output.txt\n")

if __name__ == "__main__":
    main()