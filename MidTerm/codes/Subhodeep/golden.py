#!/usr/bin/env python3
"""
golden.py - Golden model for the mic-pair delay estimator (EE2000 beamforming).

Delay Convention:
    score[k] = sum_n a[n] * b[n+k]   (samples outside the window are zero-padded)
    Positive lag means signal B is delayed relative to signal A.

Methods:
    td-plain : Standard time-domain cross-correlation (Specification V1)
    td-diff1 : Time-domain correlation preceded by first-difference filtering (Specification V2)
    fft-phat : Frequency-domain GCC-PHAT cross-correlation

Precisions:
    full  : Standard float64 arithmetic, no fixed-point effects
    16bit : Fixed-point arithmetic matching spec (32-bit products, 42-bit accumulator)
    1bit  : Sign-only (+1 / -1) correlation, matching hardware 1-bit modes
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Precision Mode Descriptors
PRECISION_FULL = "full"
PRECISION_16BIT = "16bit"
PRECISION_1BIT = "1bit"
PRECISION_MODES = (PRECISION_FULL, PRECISION_16BIT, PRECISION_1BIT)

# Channel pairs to evaluate: (Reference, Target) -> lag represents Target relative to Reference
MIC_PAIRS = [(1, 2), (1, 3), (2, 3)]


@dataclass(frozen=True)
class Config:
    N: int = 1024                         # Window sample length
    max_lag: int = 13                     # Maximum lag offset to evaluate (-max_lag to +max_lag)
    in_bits: int = 16                     # Bit depth of raw input signals
    prod_bits: int = 32                   # Width of multiplier product output
    acc_bits: int = 42                    # Width of signed hardware accumulator
    nfft: int = 2048                      # FFT size (must be >= 2*N - 1)
    edge_fill: int = 0                    # Value returned when accessing out-of-bounds score neighbors
    carry_whitener_state: bool = False    # Preserve filter state between adjacent processing frames

    @property
    def lags(self) -> list[int]:
        return list(range(-self.max_lag, self.max_lag + 1))


def wrap_twos_complement(value, bits: int):
    """Wraps integer values to fit a signed two's-complement representation of given bit width."""
    modulus = 1 << bits
    half = modulus >> 1
    return ((value + half) % modulus) - half


def calculate_min_accumulator_bits(N: int, in_bits: int) -> int:
    """Calculates the minimum accumulator bit-width required to guarantee no overflow."""
    max_value = N * (1 << (2 * (in_bits - 1)))
    return max_value.bit_length() + 1


# ----------------------------------------------------------------------------------------
# I/O Helpers
# ----------------------------------------------------------------------------------------

def read_memory_file(filepath, width: int = 16, radix: int = 16) -> np.ndarray:
    """Parses Verilog hex/binary memory files ($readmemh / $readmemb) into signed numpy arrays."""
    text = Path(filepath).read_text()
    # Strip comments
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    
    parsed_words: list[int] = []
    for token in text.split():
        if token.startswith("@"):
            target_addr = int(token[1:].replace("_", ""), 16)
            if target_addr > len(parsed_words):
                parsed_words.extend([0] * (target_addr - len(parsed_words)))
            continue

        clean_token = token.replace("_", "")
        if re.search(r"[xXzZ?]", clean_token):
            warnings.warn(f"Uninitialized value '{token}' in {filepath} replaced with 0.")
            raw_val = 0
        else:
            raw_val = int(clean_token, radix)

        if raw_val >> width:
            raise ValueError(f"Value '{token}' in {filepath} exceeds max width of {width} bits.")

        # Convert unsigned representation to signed two's complement
        if raw_val >= (1 << (width - 1)):
            raw_val -= (1 << width)
        parsed_words.append(raw_val)

    return np.array(parsed_words, dtype=np.int64)


def write_memory_file(filepath, values, width: int = 16) -> None:
    """Dumps an array of numbers to a standard Verilog hex memory file."""
    bit_mask = (1 << width) - 1
    hex_digits = width // 4
    lines = [f"{int(v) & bit_mask:0{hex_digits}X}" for v in values]
    Path(filepath).write_text("\n".join(lines) + "\n")


def read_expected_lags(filepath) -> dict:
    """Extracts expected ground-truth lags from benchmark JSON files."""
    data = json.loads(Path(filepath).read_text())
    extracted_lags: dict = {}

    def extract_pairs(item, key_path=""):
        if isinstance(item, dict):
            for k, v in item.items():
                extract_pairs(v, f"{key_path} {k}")
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            digits = re.findall(r"\d", key_path)
            if len(digits) == 2:
                extracted_lags[(int(digits[0]), int(digits[1]))] = item

    extract_pairs(data)
    
    # Standardize 0-indexed channels to 1-indexed (ch1, ch2, ch3)
    if extracted_lags and min(min(pair) for pair in extracted_lags) == 0:
        extracted_lags = {(ch_a + 1, ch_b + 1): val for (ch_a, ch_b), val in extracted_lags.items()}

    if not extracted_lags:
        warnings.warn(f"Could not parse valid channel pairs from {filepath}")
    return extracted_lags


# ----------------------------------------------------------------------------------------
# Signal Processing Filters
# ----------------------------------------------------------------------------------------

def apply_first_difference(signal: np.ndarray, saturation_bits: int | None = None) -> np.ndarray:
    """Applies a derivative filter y[n] = x[n] - x[n-1] with optional fixed-point saturation."""
    prior_samples = np.concatenate((np.zeros(1, dtype=signal.dtype), signal[:-1]))
    diff = signal - prior_samples
    
    if saturation_bits is not None:
        limit = 1 << (saturation_bits - 1)
        diff = np.clip(diff, -limit, limit - 1)
    return diff


WHITENERS = {
    "none": None,
    "diff1": apply_first_difference
}

PROCESSING_PIPELINES = {
    "td-plain": ("td", "none"),
    "td-diff1": ("td", "diff1"),
    "fft-phat": ("fft", "none"),
}


def preprocess_signal(signal, filter_func, precision: str, cfg: Config) -> np.ndarray:
    """Prepares raw inputs according to requested arithmetic precision and pre-filtering."""
    raw = np.asarray(signal, dtype=np.int64)
    min_val, max_val = -(1 << (cfg.in_bits - 1)), (1 << (cfg.in_bits - 1)) - 1
    
    if precision != PRECISION_FULL and (raw.min() < min_val or raw.max() > max_val):
        raise ValueError(f"Input values violate the {cfg.in_bits}-bit dynamic range bounds.")

    if precision == PRECISION_FULL:
        float_signal = raw.astype(np.float64)
        return filter_func(float_signal, None) if filter_func else float_signal

    filtered = filter_func(raw, cfg.in_bits) if filter_func else raw
    if precision == PRECISION_1BIT:
        return np.where(filtered >= 0, 1, -1).astype(np.int64)
    
    return filtered


# ----------------------------------------------------------------------------------------
# Correlation Implementations
# ----------------------------------------------------------------------------------------

def compute_cross_correlation_reference(sig_a, sig_b, cfg: Config):
    """Step-by-step cross correlation mimicking discrete register updates in hardware."""
    window_len = cfg.N
    sig_a = [int(v) for v in sig_a]
    sig_b = [int(v) for v in sig_b]
    scores = []
    overflow_detected = False

    for lag in cfg.lags:
        accumulator = 0
        exact_sum = 0
        for n in range(window_len):
            target_idx = n + lag
            prod = sig_a[n] * sig_b[target_idx] if 0 <= target_idx < window_len else 0
            
            wrapped_prod = wrap_twos_complement(prod, cfg.prod_bits)
            overflow_detected |= (wrapped_prod != prod)
            exact_sum += wrapped_prod
            
            accumulator = wrap_twos_complement(accumulator + wrapped_prod, cfg.acc_bits)

        overflow_detected |= (accumulator != exact_sum)
        scores.append(accumulator)

    return scores, overflow_detected


def compute_cross_correlation_fast(sig_a, sig_b, cfg: Config):
    """Vectorized calculation yielding bit-exact results identical to hardware accumulators."""
    window_len = cfg.N
    sig_a = np.asarray(sig_a, dtype=np.int64)
    sig_b = np.asarray(sig_b, dtype=np.int64)
    scores = []
    overflow_detected = False

    for lag in cfg.lags:
        if lag >= 0:
            products = sig_a[:window_len - lag] * sig_b[lag:]
        else:
            products = sig_a[-lag:] * sig_b[:window_len + lag]

        wrapped_prods = wrap_twos_complement(products, cfg.prod_bits)
        total_sum = int(wrapped_prods.sum())
        wrapped_sum = int(wrap_twos_complement(total_sum, cfg.acc_bits))

        overflow_detected |= bool((wrapped_prods != products).any()) or (wrapped_sum != total_sum)
        scores.append(wrapped_sum)

    return scores, overflow_detected


def compute_cross_correlation_float(sig_a, sig_b, cfg: Config):
    """Ideal floating-point cross-correlation implementation."""
    window_len = cfg.N
    scores = []
    for lag in cfg.lags:
        if lag >= 0:
            val = float(np.dot(sig_a[:window_len - lag], sig_b[lag:]))
        else:
            val = float(np.dot(sig_a[-lag:], sig_b[:window_len + lag]))
        scores.append(val)
    return scores, False


def compute_gcc_phat(sig_a, sig_b, cfg: Config):
    """Computes Generalized Cross-Correlation with Phase Transform (GCC-PHAT)."""
    if cfg.nfft < 2 * cfg.N - 1:
        raise ValueError("FFT length must be at least 2*N - 1 to prevent cyclic convolution overlap.")

    fft_a = np.fft.rfft(np.asarray(sig_a, dtype=np.float64), cfg.nfft)
    fft_b = np.fft.rfft(np.asarray(sig_b, dtype=np.float64), cfg.nfft)
    
    cross_spectrum = np.conj(fft_a) * fft_b
    magnitude = np.abs(cross_spectrum)
    
    normalized_spectrum = np.divide(
        cross_spectrum, 
        magnitude, 
        out=np.zeros_like(cross_spectrum), 
        where=magnitude > (1e-12 * magnitude.max())
    )
    
    time_domain = np.fft.irfft(normalized_spectrum, cfg.nfft)
    scores = [float(time_domain[lag % cfg.nfft]) for lag in cfg.lags]
    return scores, False


@dataclass
class PeakSearchResult:
    best_lag: int
    score_minus_1: float
    score_best: float
    score_plus_1: float
    is_at_edge: bool
    overflow: bool = False


def find_best_lag(scores: list[float], cfg: Config) -> PeakSearchResult:
    """Finds peak correlation value and surrounding neighbor metrics."""
    best_idx = 0
    for idx, score in enumerate(scores):
        if score > scores[best_idx]:  # Strict comparison prioritizes lower lags on ties
            best_idx = idx

    def get_score_at(idx):
        return scores[idx] if 0 <= idx < len(scores) else cfg.edge_fill

    return PeakSearchResult(
        best_lag=cfg.lags[best_idx],
        score_minus_1=get_score_at(best_idx - 1),
        score_best=scores[best_idx],
        score_plus_1=get_score_at(best_idx + 1),
        is_at_edge=(best_idx == 0 or best_idx == len(scores) - 1)
    )


def compute_window_correlation(sig_a, sig_b, method_kind: str, cfg: Config, use_reference_impl: bool):
    if method_kind == "fft":
        return compute_gcc_phat(sig_a, sig_b, cfg)
    if sig_a.dtype.kind == "f":
        return compute_cross_correlation_float(sig_a, sig_b, cfg)
    
    if use_reference_impl:
        return compute_cross_correlation_reference(sig_a, sig_b, cfg)
    return compute_cross_correlation_fast(sig_a, sig_b, cfg)


def chunk_signal(signal, window_size: int):
    """Splits an input stream into non-overlapping contiguous frames."""
    total_windows = len(signal) // window_size
    return [signal[i * window_size : (i + 1) * window_size] for i in range(total_windows)]


def process_channel_pair(stream_a, stream_b, method: str, precision: str, cfg: Config, use_reference_impl: bool = False) -> list[PeakSearchResult]:
    """Runs end-to-end estimation pipeline over frame chunks for two given input channels."""
    method_kind, filter_name = PROCESSING_PIPELINES[method]
    filter_func = WHITENERS[filter_name]

    if cfg.carry_whitener_state:
        frames_a = chunk_signal(preprocess_signal(stream_a, filter_func, precision, cfg), cfg.N)
        frames_b = chunk_signal(preprocess_signal(stream_b, filter_func, precision, cfg), cfg.N)
    else:
        frames_a = [preprocess_signal(chunk, filter_func, precision, cfg) for chunk in chunk_signal(np.asarray(stream_a), cfg.N)]
        frames_b = [preprocess_signal(chunk, filter_func, precision, cfg) for chunk in chunk_signal(np.asarray(stream_b), cfg.N)]

    results = []
    for frame_a, frame_b in zip(frames_a, frames_b):
        scores, overflow = compute_window_correlation(frame_a, frame_b, method_kind, cfg, use_reference_impl)
        res = find_best_lag(scores, cfg)
        res.overflow = overflow
        results.append(res)
        
    return results


# ----------------------------------------------------------------------------------------
# Test Harness & Evaluation Commands
# ----------------------------------------------------------------------------------------

def format_lags_preview(lag_list, max_display=8):
    formatted = " ".join(f"{l:+d}" for l in lag_list[:max_display])
    if len(lag_list) > max_display:
        formatted += " ..."
    return formatted


def run_test_directory(target_dir, cfg: Config, radix: int = 16, use_reference_impl: bool = False, output_prefix: str | None = None, quiet: bool = False):
    dir_path = Path(target_dir)
    mem_filenames = [f"ch{i}.mem" for i in (1, 2, 3)]
    
    # Fallback support for 0-indexed memory filenames
    if not (dir_path / mem_filenames[0]).exists() and (dir_path / "mic0.mem").exists():
        mem_filenames = [f"mic{i}.mem" for i in (0, 1, 2)]

    channel_data = {ch_idx: read_memory_file(dir_path / fname, radix=radix) for ch_idx, fname in zip((1, 2, 3), mem_filenames)}
    min_length = min(len(data) for data in channel_data.values())

    if any(len(data) != min_length for data in channel_data.values()):
        warnings.warn(f"Channel length mismatch detected. Truncating signals to {min_length} samples.")

    channel_data = {ch_idx: data[:min_length] for ch_idx, data in channel_data.items()}
    leftover_samples = min_length % cfg.N
    if leftover_samples:
        warnings.warn(f"Ignoring {leftover_samples} trailing sample(s) that do not fill a complete frame.")

    truth_file = next((p for p in (dir_path / "expected.json", dir_path / "ground_truth.json") if p.exists()), None)
    expected_lags = read_expected_lags(truth_file) if truth_file else {}

    if not quiet:
        print(f"Loaded {min_length} samples/channel -> {min_length // cfg.N} window(s) (N={cfg.N}, max_lag=±{cfg.max_lag})")

    records = []
    for ch_a, ch_b in MIC_PAIRS:
        expected = expected_lags.get((ch_a, ch_b))
        if not quiet:
            print(f"\nProcessing Pair: ch{ch_a} -> ch{ch_b} | Target Lag: {expected if expected is not None else 'N/A'}")

        for method in PROCESSING_PIPELINES:
            for precision in PRECISION_MODES:
                results = process_channel_pair(channel_data[ch_a], channel_data[ch_b], method, precision, cfg, use_reference_impl)
                
                for frame_idx, res in enumerate(results):
                    records.append({
                        "pair": f"ch{ch_a}-ch{ch_b}",
                        "window": frame_idx,
                        "method": method,
                        "precision": precision,
                        "best_lag": res.best_lag,
                        "score_m1": res.score_minus_1,
                        "score_0": res.score_best,
                        "score_p1": res.score_plus_1,
                        "at_edge": res.is_at_edge,
                        "overflow": res.overflow,
                    })

                if not quiet:
                    detected_lags = [r.best_lag for r in results]
                    accuracy_str = ""
                    if expected is not None:
                        hits = sum(l == round(expected) for l in detected_lags)
                        accuracy_str = f"hit {hits}/{len(detected_lags)}"

                    print(f"  {method:9s} {precision:6s} {accuracy_str:9s} {format_lags_preview(detected_lags)}")

    if output_prefix:
        Path(f"{output_prefix}.json").write_text(json.dumps(records, indent=1))
        with open(f"{output_prefix}.csv", "w", newline="", encoding="ascii") as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0].keys()), lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
        if not quiet:
            print(f"\nExported dataset results to {output_prefix}.csv and {output_prefix}.json ({len(records)} entries)")

    return records


def run_monte_carlo_simulation(trials: int, snr_db: float, cfg: Config, seed: int = 0):
    """Evaluates estimation error rate against synthetic AR(1) noisy signals across methods."""
    rng = np.random.default_rng(seed)
    channel_delays = (0, 3, 6)
    ground_truth = {(1, 2): 3, (1, 3): 6}
    
    failures = {(m, p): 0 for m in PROCESSING_PIPELINES for p in PRECISION_MODES}
    total_evaluations = 0
    padding = 64

    for _ in range(trials):
        white_noise = rng.standard_normal(cfg.N + padding)
        source_signal = np.empty_like(white_noise)
        source_signal[0] = white_noise[0]
        
        for n in range(1, len(white_noise)):
            source_signal[n] = 0.9 * source_signal[n - 1] + white_noise[n]

        source_signal *= 4000.0 / source_signal.std()
        noise_level = 4000.0 * 10 ** (-snr_db / 20)

        mic_signals = {}
        for idx, delay_offset in enumerate(channel_delays, start=1):
            raw_sample = source_signal[padding - delay_offset : padding - delay_offset + cfg.N] + rng.normal(0, noise_level, cfg.N)
            mic_signals[idx] = np.clip(np.round(raw_sample), -32768, 32767).astype(np.int64)

        for (ch_a, ch_b), true_lag in ground_truth.items():
            total_evaluations += 1
            for method in PROCESSING_PIPELINES:
                for precision in PRECISION_MODES:
                    res = process_channel_pair(mic_signals[ch_a], mic_signals[ch_b], method, precision, cfg)[0]
                    if res.best_lag != true_lag:
                        failures[(method, precision)] += 1

    print(f"\nMonte-Carlo Miss Rates ({trials} iterations x 2 pairs, SNR = {snr_db} dB, N = {cfg.N})")
    print(f"  {'Method':10s}" + "".join(f"{p:>9s}" for p in PRECISION_MODES))
    for method in PROCESSING_PIPELINES:
        row = "".join(f"{100 * failures[(method, p)] / total_evaluations:8.1f}%" for p in PRECISION_MODES)
        print(f"  {method:10s}{row}")


# ----------------------------------------------------------------------------------------
# Self-Test Verification suite
# ----------------------------------------------------------------------------------------

def run_self_tests() -> None:
    rng = np.random.default_rng(1234)
    cfg = Config()

    # Math validations
    assert calculate_min_accumulator_bits(1024, 16) == 42
    assert wrap_twos_complement(2 ** 40, 42) == 2 ** 40 
    assert wrap_twos_complement(2 ** 40, 41) == -2 ** 40

    # Accumulation boundaries
    full_scale = np.full(cfg.N, -32768, dtype=np.int64)
    scores_42, ovf_42 = compute_cross_correlation_fast(full_scale, full_scale, cfg)
    assert scores_42[cfg.max_lag] == 2 ** 40 and not ovf_42
    
    scores_41, ovf_41 = compute_cross_correlation_fast(full_scale, full_scale, Config(acc_bits=41))
    assert scores_41[cfg.max_lag] == -2 ** 40 and ovf_41
    assert compute_cross_correlation_reference(full_scale, full_scale, cfg) == (scores_42, ovf_42)

    # Reference vs vectorized implementation parity
    rand_a = rng.integers(-32768, 32768, cfg.N)
    rand_b = rng.integers(-32768, 32768, cfg.N)
    assert compute_cross_correlation_reference(rand_a, rand_b, cfg) == compute_cross_correlation_fast(rand_a, rand_b, cfg)
    
    mini_cfg = Config(N=64, max_lag=5)
    assert compute_cross_correlation_reference(rand_a[:64], rand_b[:64], mini_cfg) == compute_cross_correlation_fast(rand_a[:64], rand_b[:64], mini_cfg)

    # Peak search behavior
    blank_peak = find_best_lag([0] * 27, cfg)
    assert blank_peak.best_lag == -13 and blank_peak.is_at_edge and blank_peak.score_minus_1 == cfg.edge_fill and blank_peak.score_plus_1 == 0
    
    test_scores = [0] * 27
    test_scores[10] = test_scores[11] = 7
    test_scores[9], test_scores[12] = 3, 4
    resolved_peak = find_best_lag(test_scores, cfg)
    assert (resolved_peak.best_lag, resolved_peak.score_minus_1, resolved_peak.score_best, resolved_peak.score_plus_1) == (-13 + 10, 3, 7, 7)

    # Out-of-window isolation check
    impulse_a = np.zeros(cfg.N, np.int64); impulse_a[0] = 1
    impulse_b = np.zeros(cfg.N, np.int64); impulse_b[-1] = 1
    assert set(compute_cross_correlation_fast(impulse_a, impulse_b, cfg)[0]) == {0}

    # Filter functionality
    diff_test = apply_first_difference(np.array([-32768, 32767, 0], dtype=np.int64), 16)
    assert list(diff_test) == [-32768, 32767, -32767]
    
    diff_sat = apply_first_difference(np.array([32767, -32768], dtype=np.int64), 16)
    assert list(diff_sat) == [32767, -32768]
    
    combined = np.concatenate((rand_a[:cfg.N], rand_b[:cfg.N]))
    chunks = chunk_signal(combined, cfg.N)
    assert preprocess_signal(chunks[1], apply_first_difference, PRECISION_16BIT, cfg)[0] == chunks[1][0]
    assert apply_first_difference(combined, 16)[cfg.N] == np.clip(combined[cfg.N] - combined[cfg.N - 1], -32768, 32767)

    # Binary correlation property check
    bin_a, bin_b = np.where(rand_a >= 0, 1, -1), np.where(rand_b >= 0, 1, -1)
    lag_shift = 3
    expected_matches = (cfg.N - lag_shift) - 2 * int((bin_a[:cfg.N - lag_shift] != bin_b[lag_shift:]).sum())
    assert compute_cross_correlation_fast(bin_a, bin_b, cfg)[0][cfg.max_lag + lag_shift] == expected_matches

    # File read/write verification
    with tempfile.TemporaryDirectory() as temp_dir:
        sample_data = rng.integers(-32768, 32768, 100)
        file_path = Path(temp_dir) / "test_mem.mem"
        write_memory_file(file_path, sample_data)
        assert (read_memory_file(file_path) == sample_data).all()

    # Synthetic signal delay recovery across options
    for shift in (5, -4, 0, 13, -13):
        base_signal = rng.integers(-8000, 8000, cfg.N)
        
        # Apply delay offset
        shifted_signal = np.zeros_like(base_signal)
        if shift >= 0:
            shifted_signal[shift:] = base_signal[:len(base_signal) - shift]
        else:
            shifted_signal[:shift] = base_signal[-shift:]

        for m in PROCESSING_PIPELINES:
            for p in PRECISION_MODES:
                predicted_lag = process_channel_pair(base_signal, shifted_signal, m, p, cfg)[0].best_lag
                assert predicted_lag == shift, f"Failure in {m}/{p}: Expected shift {shift}, calculated {predicted_lag}"

    # Directory processing test run
    with tempfile.TemporaryDirectory() as temp_dir:
        synthetic_stream = rng.integers(-6000, 6000, 3 * cfg.N)
        
        def apply_delay(arr, d):
            res = np.zeros_like(arr)
            if d >= 0:
                res[d:] = arr[:len(arr) - d]
            else:
                res[:d] = arr[-d:]
            return res

        channels = {
            1: synthetic_stream, 
            2: apply_delay(synthetic_stream, 3), 
            3: apply_delay(synthetic_stream, 6)
        }
        
        for ch_idx, data in channels.items():
            noisy_data = np.clip(data + rng.integers(-50, 50, len(data)), -32768, 32767)
            write_memory_file(Path(temp_dir) / f"ch{ch_idx}.mem", noisy_data)

        Path(temp_dir, "expected.json").write_text(json.dumps({"1-2": 3, "1-3": 6, "2-3": 3}))
        
        fast_results = run_test_directory(temp_dir, cfg, quiet=True)
        expected_pair_lags = {"ch1-ch2": 3, "ch1-ch3": 6, "ch2-ch3": 3}
        assert all(r["best_lag"] == expected_pair_lags[r["pair"]] for r in fast_results), "Directory processing output mismatch"
        
        ref_results = run_test_directory(temp_dir, cfg, use_reference_impl=True, quiet=True)
        sort_key = lambda r: (r["pair"], r["window"], r["method"], r["precision"])
        assert sorted(fast_results, key=sort_key) == sorted(ref_results, key=sort_key), "Fast vs Reference results disagree"

    print("ALL TESTS COMPLETED SUCCESSFULLY.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", help="Directory containing ch1.mem, ch2.mem, ch3.mem")
    parser.add_argument("--selftest", action="store_true", help="Run integration and sanity tests")
    parser.add_argument("--mc", type=int, metavar="TRIALS", help="Run Monte-Carlo evaluation over specified trial count")
    parser.add_argument("--snr", type=float, default=0.0, help="Signal-to-noise ratio in dB for Monte-Carlo test")
    parser.add_argument("--N", type=int, default=1024, help="Correlation window size")
    parser.add_argument("--max-lag", type=int, default=13, help="Maximum lag search bound")
    parser.add_argument("--acc-bits", type=int, default=42, help="Signed hardware accumulator size")
    parser.add_argument("--edge-fill", type=int, default=0, help="Fill value for scores out of bounds")
    parser.add_argument("--carry-whitener-state", action="store_true", help="Maintain whitening state across windows")
    parser.add_argument("--radix", type=int, default=16, choices=(2, 16), help="Input file radix (16=hex, 2=binary)")
    parser.add_argument("--ref", action="store_true", help="Use un-vectorized discrete register loop implementation")
    parser.add_argument("--out", help="File prefix for saving results (.csv and .json)")
    args = parser.parse_args()

    # Calculate optimal FFT length to prevent cyclic convolution overlapping
    fft_size = 1 << (2 * args.N - 1).bit_length()
    cfg = Config(
        N=args.N, 
        max_lag=args.max_lag, 
        acc_bits=args.acc_bits, 
        edge_fill=args.edge_fill,
        carry_whitener_state=args.carry_whitener_state, 
        nfft=fft_size
    )
    
    min_bits_needed = calculate_min_accumulator_bits(cfg.N, cfg.in_bits)
    if cfg.acc_bits < min_bits_needed:
        warnings.warn(f"Accumulator width ({cfg.acc_bits} bits) is less than the required safe width ({min_bits_needed} bits). Overflow may occur.")

    if args.selftest:
        run_self_tests()
    elif args.mc:
        run_monte_carlo_simulation(args.mc, args.snr, cfg)
    elif args.dir:
        run_test_directory(args.dir, cfg, radix=args.radix, use_reference_impl=args.ref, output_prefix=args.out)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())