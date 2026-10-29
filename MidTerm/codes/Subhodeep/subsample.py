#!/usr/bin/env python3
"""
subsample_replication.py  --  EE2000 team #4, mid-term report, task 3
=====================================================================
Replicates the sub-sample peak-interpolation results of the literature
review (Cespedes et al. 1995, Jacovitti & Scarano 1993, Laakso et al. 1996)
and measures them on OUR correlator score definition (golden-model eq. (5)):

        s[k] = sum_n a[n] * b[n+k],   k in {-13..13},   b delayed by D => peak at k = D

Usage:   python subsample_replication.py [--trials 400] [--plots] [--out results.json]
Needs:   numpy (scipy optional, matplotlib optional for --plots).
Assumed sampling rate for the angle conversion: FS = 48 kHz (confirm with mic datasheet).
"""
import argparse
import json
import numpy as np

try:
    from scipy.signal import lfilter
except ImportError:
    lfilter = None

SPEED_OF_SOUND = 343.0     # m/s
FS = 48_000.0              # Sampling frequency (Hz)
MAX_LAG = 13               # Lag range K = {-13..13}
SINC_HALF_WIDTH = 4        # Sinc half width (total taps = 8)
LAG_BUFFER = MAX_LAG + 1 + SINC_HALF_WIDTH + 1  # Lag buffer radius


# ============================================================================
# Peak Interpolation Estimators (3-Point Fits & Reconstructive Search)
# ============================================================================

def interpolate_parabola(y_left, y_peak, y_right):
    """Find peak offset using 3-point parabolic interpolation."""
    denominator = 2.0 * (2.0 * y_peak - y_right - y_left)
    if denominator <= 0:
        return 0.0
    return (y_right - y_left) / denominator


def interpolate_cosine(y_left, y_peak, y_right):
    """Find peak offset using 3-point cosine fitting (Cespedes et al.)."""
    if y_peak <= 0:
        return interpolate_parabola(y_left, y_peak, y_right)
    
    ratio = (y_left + y_right) / (2.0 * y_peak)
    if not (-1.0 < ratio < 1.0):
        return interpolate_parabola(y_left, y_peak, y_right)
    
    frequency = np.arccos(ratio)
    phase = np.arctan2(y_left - y_right, 2.0 * y_peak * np.sin(frequency))
    return -phase / frequency


def interpolate_triangle(y_left, y_peak, y_right):
    """Find peak offset assuming a symmetric triangular peak."""
    denominator = 2.0 * (y_peak - min(y_left, y_right))
    if denominator <= 0:
        return 0.0
    return (y_right - y_left) / denominator


def evaluate_sinc_interpolation(scores, target_time):
    """Evaluate Blackman-windowed sinc interpolation at a target continuous lag."""
    base_index = int(np.floor(target_time))
    tap_indices = np.arange(base_index - SINC_HALF_WIDTH + 1, base_index + SINC_HALF_WIDTH + 1)
    time_deltas = target_time - tap_indices
    
    # Blackman window computation
    normalized_deltas = time_deltas / SINC_HALF_WIDTH
    window = 0.42 + 0.5 * np.cos(np.pi * normalized_deltas) + 0.08 * np.cos(2 * np.pi * normalized_deltas)
    
    sample_values = scores[LAG_BUFFER + tap_indices]
    return float(np.sum(sample_values * np.sinc(time_deltas) * window))


def interpolate_sinc_bisection(scores, peak_lag, iterations=4):
    """Refine peak location via iterative binary search with sinc interpolation."""
    left = peak_lag - 1.0
    center = float(peak_lag)
    right = peak_lag + 1.0
    center_val = scores[LAG_BUFFER + peak_lag]

    for _ in range(iterations):
        mid_left = 0.5 * (left + center)
        mid_right = 0.5 * (center + right)
        
        val_left = evaluate_sinc_interpolation(scores, mid_left)
        val_right = evaluate_sinc_interpolation(scores, mid_right)

        if val_left > center_val and val_left >= val_right:
            right, center, center_val = center, mid_left, val_left
        elif val_right > center_val:
            left, center, center_val = center, mid_right, val_right
        else:
            left, right = mid_left, mid_right

    return center - peak_lag


# ============================================================================
# Signal Generation & Cross-Correlation Pipeline
# ============================================================================

def generate_base_signal(rng, num_samples, source_type, bandwidth_ratio):
    """Generate synthetic reference audio signal (AR1 autoregressive or lowpass noise)."""
    if source_type == "ar1":
        raw_noise = rng.standard_normal(num_samples + 200)
        if lfilter is not None:
            signal = lfilter([1.0], [1.0, -0.9], raw_noise)[200:]
        else:
            signal = np.zeros(num_samples + 200)
            for i in range(1, len(raw_noise)):
                signal[i] = raw_noise[i] + 0.9 * signal[i - 1]
            signal = signal[200:]
    else:
        spectrum = np.fft.rfft(rng.standard_normal(num_samples))
        frequencies = np.fft.rfftfreq(num_samples)
        spectrum[frequencies > 0.5 * bandwidth_ratio] = 0.0
        signal = np.fft.irfft(spectrum, num_samples)
        
    return signal / np.std(signal)


def generate_signal_pair(rng, length, delay, source_type, bandwidth_ratio, snr_db, padding=64):
    """Create a pair of correlated signals delayed by fractional delay D using FFT phase shifts."""
    padded_length = length + 2 * padding
    ref_padded = generate_base_signal(rng, padded_length, source_type, bandwidth_ratio)
    
    # Apply fractional delay shift in frequency domain
    ref_fft = np.fft.rfft(ref_padded)
    frequencies = np.fft.rfftfreq(padded_length)
    delayed_fft = ref_fft * np.exp(-2j * np.pi * frequencies * delay)
    delayed_fft[-1] = 0.0
    delayed_padded = np.fft.irfft(delayed_fft, padded_length)

    # Crop out boundary artifacts
    sig_a = ref_padded[padding:padding + length].copy()
    sig_b = delayed_padded[padding:padding + length].copy()

    # Inject Gaussian noise matching target SNR
    if np.isfinite(snr_db):
        noise_scale = 10.0 ** (-snr_db / 20.0)
        sig_a += rng.standard_normal(length) * np.std(sig_a) * noise_scale
        sig_b += rng.standard_normal(length) * np.std(sig_b) * noise_scale

    return sig_a, sig_b


def compute_correlation_scores(sig_a, sig_b, apply_whitening, is_one_bit):
    """Compute cross-correlation lag sequence s[k]."""
    if apply_whitening:
        sig_a = np.concatenate(([sig_a[0]], np.diff(sig_a)))
        sig_b = np.concatenate(([sig_b[0]], np.diff(sig_b)))
        
    if is_one_bit:
        sig_a = np.sign(sig_a)
        sig_a[sig_a == 0] = 1.0
        sig_b = np.sign(sig_b)
        sig_b[sig_b == 0] = 1.0

    num_samples = len(sig_a)
    raw_corr = np.correlate(sig_b, sig_a, mode="full")
    center_idx = num_samples - 1
    scores = raw_corr[center_idx - LAG_BUFFER : center_idx + LAG_BUFFER + 1]

    if is_one_bit:
        lag_offsets = np.arange(-LAG_BUFFER, LAG_BUFFER + 1)
        overlap_lengths = num_samples - np.abs(lag_offsets)
        scores /= overlap_lengths

    return scores


def estimate_fractional_delay(scores, is_one_bit):
    """Extract integer lag peak and apply sub-sample interpolation methods."""
    search_window = scores[LAG_BUFFER - MAX_LAG : LAG_BUFFER + MAX_LAG + 1]
    peak_lag = int(np.argmax(search_window)) - MAX_LAG

    neighbors = (
        scores[LAG_BUFFER + peak_lag - 1],
        scores[LAG_BUFFER + peak_lag],
        scores[LAG_BUFFER + peak_lag + 1]
    )

    estimates = {
        "integer": float(peak_lag),
        "parab": peak_lag + interpolate_parabola(*neighbors),
        "cosfit": peak_lag + interpolate_cosine(*neighbors)
    }

    if is_one_bit:
        # Invert Van Vleck arcsine transformation
        linearized_scores = np.sin(0.5 * np.pi * scores)
        lin_neighbors = (
            linearized_scores[LAG_BUFFER + peak_lag - 1],
            linearized_scores[LAG_BUFFER + peak_lag],
            linearized_scores[LAG_BUFFER + peak_lag + 1]
        )
        
        # Jacovitti AMDF square transformation
        mismatch_scores = -((1.0 - scores) / 2.0) ** 2
        mismatch_neighbors = (
            mismatch_scores[LAG_BUFFER + peak_lag - 1],
            mismatch_scores[LAG_BUFFER + peak_lag],
            mismatch_scores[LAG_BUFFER + peak_lag + 1]
        )

        estimates["parab_lin"] = peak_lag + interpolate_parabola(*lin_neighbors)
        estimates["parab_m2"] = peak_lag + interpolate_parabola(*mismatch_neighbors)
        estimates["tri"] = peak_lag + interpolate_triangle(*neighbors)
    else:
        estimates["sinc4"] = peak_lag + interpolate_sinc_bisection(scores, peak_lag)

    return peak_lag, estimates


# ============================================================================
# Theoretical Bias Replication & Evaluation
# ============================================================================

def closed_form_parabolic_bias_cosine(delta, omega_ts):
    """Closed-form parabolic bias on a pure cosine ACF."""
    return 0.5 / np.tan(omega_ts / 2.0) * np.tan(omega_ts * delta) - delta


def closed_form_parabolic_bias_gaussian(delta, spacing, width):
    """Closed-form parabolic bias on a Gaussian ACF (Jacovitti & Scarano)."""
    normalized_delta = spacing * delta / (width ** 2)
    numerator = 0.5 * spacing * np.sinh(normalized_delta)
    denominator = np.exp(spacing ** 2 / (2.0 * width ** 2)) - np.cosh(normalized_delta)
    return (numerator / denominator) - delta


def evaluate_ideal_bias(shape_type, fs_over_f0, deltas):
    """Compute empirical bias across target fractional offsets on ideal ACFs."""
    f0 = 1.0 / fs_over_f0
    if shape_type == "cos":
        acf_function = lambda t: np.cos(2 * np.pi * f0 * t)
    else:
        sigma = 0.3 * f0
        acf_function = lambda t: np.exp(-2 * (np.pi * sigma * t) ** 2) * np.cos(2 * np.pi * f0 * t)

    results = {"parab": [], "cosfit": []}
    for offset in deltas:
        samples = [acf_function(i - offset) for i in (-1, 0, 1)]
        results["parab"].append(interpolate_parabola(*samples) - offset)
        results["cosfit"].append(interpolate_cosine(*samples) - offset)

    return {key: np.array(values) for key, values in results.items()}


def run_part_a():
    """Run Part A: Verify theoretical vs closed-form bias formulas."""
    deltas = np.linspace(0.0, 0.5, 101)
    results = {"selftest": {}, "ideal": {}}

    # Closed-form vs numeric validation
    w_ts = 2 * np.pi / 10
    numeric_cos = evaluate_ideal_bias("cos", 10, deltas)["parab"]
    results["selftest"]["cos_closed_vs_numeric"] = float(
        np.max(np.abs(numeric_cos - closed_form_parabolic_bias_cosine(deltas, w_ts)))
    )

    width, spacing = 2.0, 1.0
    gauss_acf = lambda t: np.exp(-(t ** 2) / (2.0 * width ** 2))
    numeric_jacovitti = np.array([
        interpolate_parabola(gauss_acf(-spacing - d), gauss_acf(-d), gauss_acf(spacing - d)) * spacing - d
        for d in deltas
    ])
    results["selftest"]["jacovitti_gauss_closed_vs_numeric"] = float(
        np.max(np.abs(numeric_jacovitti - closed_form_parabolic_bias_gaussian(deltas, spacing, width)))
    )

    results["selftest"]["cosfit_exact_on_cos_ccf"] = float(
        np.max(np.abs(evaluate_ideal_bias("cos", 10, deltas)["cosfit"]))
    )

    # Calculate worst-case bias across sampling ratios
    for shape in ("cos", "gauss"):
        for ratio in (4, 6, 10, 20):
            bias_data = evaluate_ideal_bias(shape, ratio, deltas)
            for method in ("parab", "cosfit"):
                max_idx = int(np.argmax(np.abs(bias_data[method])))
                results["ideal"][f"{shape}_fs/f0={ratio}_{method}"] = {
                    "max_abs_bias_samples": float(abs(bias_data[method][max_idx])),
                    "at_delta": float(deltas[max_idx])
                }

    return results, deltas


# ============================================================================
# Part B: Monte Carlo Simulations
# ============================================================================

CONFIGURATIONS = {
    "lp25_V1": ("lp", 0.25, False, False),
    "lp25_V2": ("lp", 0.25, True, False),
    "ar1_V1": ("ar1", None, False, False),
    "lp25_V1_1bit": ("lp", 0.25, False, True),
    "ar1_V1_1bit": ("ar1", None, False, True),
}


def run_monte_carlo_rms(config_name, snr_db, num_trials, seed, sample_length=1024):
    """Measure RMS error of delay estimators under noise."""
    source_type, bandwidth, apply_whitening, is_one_bit = CONFIGURATIONS[config_name]
    rng = np.random.default_rng(seed)
    
    errors = {}
    anomaly_count = 0

    for _ in range(num_trials):
        true_delay = 3.0 + rng.uniform(-0.5, 0.5)
        sig_a, sig_b = generate_signal_pair(rng, sample_length, true_delay, source_type, bandwidth, snr_db)
        scores = compute_correlation_scores(sig_a, sig_b, apply_whitening, is_one_bit)
        peak_lag, est_dict = estimate_fractional_delay(scores, is_one_bit)

        # Flag coarse integer peak errors (anomalies)
        if abs(peak_lag - 3) > 1:
            anomaly_count += 1
            continue

        for method, est_val in est_dict.items():
            errors.setdefault(method, []).append(est_val - true_delay)

    rms_results = {method: float(np.sqrt(np.mean(np.square(err_list)))) for method, err_list in errors.items()}
    anomaly_rate = anomaly_count / num_trials
    return rms_results, anomaly_rate


def run_monte_carlo_bias(config_name, offsets, repetitions, seed, sample_length=1024):
    """Measure systematic estimation bias across fixed fractional offsets in noise-free conditions."""
    source_type, bandwidth, apply_whitening, is_one_bit = CONFIGURATIONS[config_name]
    rng = np.random.default_rng(seed)
    bias_table = {}

    for offset in offsets:
        accumulated_errors = {}
        for _ in range(repetitions):
            true_delay = 3.0 + offset
            sig_a, sig_b = generate_signal_pair(rng, sample_length, true_delay, source_type, bandwidth, np.inf)
            scores = compute_correlation_scores(sig_a, sig_b, apply_whitening, is_one_bit)
            _, est_dict = estimate_fractional_delay(scores, is_one_bit)

            for method, est_val in est_dict.items():
                accumulated_errors.setdefault(method, []).append(est_val - true_delay)

        bias_table[float(offset)] = {
            method: float(np.mean(err_list)) for method, err_list in accumulated_errors.items()
        }

    return bias_table


def calculate_broadside_angle_sensitivity(baseline_meters, fs=FS, speed_of_sound=SPEED_OF_SOUND):
    """Calculate broadside bearing sensitivity (deg/sample)."""
    return float(np.degrees(speed_of_sound / (baseline_meters * fs)))


# ============================================================================
# Main Program Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Sub-sample delay estimation replication.")
    parser.add_argument("--trials", type=int, default=400, help="Number of Monte Carlo trials")
    parser.add_argument("--plots", action="store_true", help="Generate performance evaluation plots")
    parser.add_argument("--out", default="results.json", help="Output JSON path")
    args = parser.parse_args()

    output_data = {"fs_assumed_Hz": FS}

    # Execute Part A Tests
    part_a_results, deltas = run_part_a()
    output_data["A"] = part_a_results
    
    print("== Part A: Self-tests (Max Absolute Difference, Samples) ==")
    for test_name, value in part_a_results["selftest"].items():
        print(f"  {test_name:40s} {value:.2e}")

    print("\n== Part A: Worst-case |Bias| of 3-Point Fits on Ideal CCF Shapes (Samples) ==")
    for key, data in part_a_results["ideal"].items():
        print(f"  {key:28s} {data['max_abs_bias_samples']:.4f}  at delta={data['at_delta']:.2f}")

    # Physical Angle Sensitivity
    sens_8cm = calculate_broadside_angle_sensitivity(0.08)
    sens_5cm = calculate_broadside_angle_sensitivity(0.05)
    output_data["angle_deg_per_sample_broadside"] = {"8cm": sens_8cm, "5cm": sens_5cm}

    print(f"\n== Broadside Angle Error Per Sample of Delay Error (fs = {FS:.0f} Hz) ==")
    print(f"  8 cm: {sens_8cm:.2f} deg/sample   5 cm: {sens_5cm:.2f} deg/sample   (integer-only sigma = 0.289 sample)")

    # Execute Part B1 (Noiseless Bias vs Offset)
    offsets = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    output_data["B_bias"] = {}
    print("\n== Part B1: Noiseless Mean Error (Est - D, Samples) vs Fractional Offset mu ==")
    for config_name in CONFIGURATIONS:
        table = run_monte_carlo_bias(config_name, offsets, repetitions=20, seed=1)
        output_data["B_bias"][config_name] = table
        method_keys = list(next(iter(table.values())).keys())
        
        print(f" [{config_name}]  " + "  ".join(f"{key:>9s}" for key in method_keys))
        for offset in offsets:
            row_str = "  ".join(f"{table[offset][key]:+9.4f}" for key in method_keys)
            print(f"   mu={offset:.1f}   {row_str}")

    # Execute Part B2 (Monte Carlo RMS Error vs SNR)
    snr_levels = [30, 10, 0]
    output_data["B_rms"] = {}
    print("\n== Part B2: RMS Error (Samples) Over Non-Anomalous Trials, mu ~ U(-0.5, 0.5) ==")
    for config_name in CONFIGURATIONS:
        output_data["B_rms"][config_name] = {}
        for snr in snr_levels:
            rms_vals, anomaly_rate = run_monte_carlo_rms(config_name, snr, args.trials, seed=100 + snr)
            output_data["B_rms"][config_name][snr] = {"rms": rms_vals, "anomaly_rate": anomaly_rate}
            
            rms_str = "  ".join(f"{k}={v:.3f}" for k, v in rms_vals.items())
            print(f" [{config_name}] SNR={snr:2d} dB anomalies={100 * anomaly_rate:4.1f}%  {rms_str}")

    # Write output to JSON file
    with open(args.out, "w") as out_file:
        json.dump(output_data, out_file, indent=1)
    print(f"\nSaved results to {args.out}")

    # Optional Plot Generation
    if args.plots:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib is not available; skipping plot generation.")
            return

        # Figure 1: Bias vs Fractional Offset
        fig1, ax1 = plt.subplots(figsize=(4.2, 3))
        for ratio in (4, 6, 10, 20):
            bias_vals = evaluate_ideal_bias("cos", ratio, deltas)["parab"]
            ax1.plot(deltas, bias_vals, label=f"cos ccf, $f_s/f_0$={ratio}")
        
        gauss_bias = evaluate_ideal_bias("gauss", 10, deltas)["parab"]
        ax1.plot(deltas, gauss_bias, "k--", label="Gauss env., 10")
        ax1.set_xlabel("True fractional offset $\\delta$ (samples)")
        ax1.set_ylabel("Parabolic bias (samples)")
        ax1.legend(fontsize=6)
        ax1.grid(alpha=0.3)
        fig1.tight_layout()
        fig1.savefig("bias_vs_frac.png", dpi=200)

        # Figure 2: RMS Error vs SNR
        fig2, ax2 = plt.subplots(figsize=(4.2, 3))
        for method in ("integer", "parab", "cosfit", "sinc4"):
            rms_series = [output_data["B_rms"]["lp25_V1"][snr]["rms"][method] for snr in snr_levels]
            ax2.plot(snr_levels, rms_series, "o-", label=method)
        
        ax2.set_yscale("log")
        ax2.set_xlabel("SNR (dB)")
        ax2.set_ylabel("RMS delay error (samples)")
        ax2.legend(fontsize=7)
        ax2.grid(alpha=0.3)
        fig2.tight_layout()
        fig2.savefig("rms_vs_snr.png", dpi=200)

        print("Saved plots: bias_vs_frac.png, rms_vs_snr.png")


if __name__ == "__main__":
    main()