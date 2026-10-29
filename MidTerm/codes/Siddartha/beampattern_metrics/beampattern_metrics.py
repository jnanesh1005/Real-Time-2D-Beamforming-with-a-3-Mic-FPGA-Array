
"""
we evaluate OUR triangular microphone array using
an ideal, far-field, narrowband, equal-weight delay-and-sum
beamformer.
"""

import csv
import numpy as np
import matplotlib.pyplot as plt


C = 343.0  # Speed of sound (m/s)

FREQUENCIES = [500, 1000, 2000, 4000]

# Microphone coordinates in metres.
# Same three-microphone geometry as our simulator.
MIC_POSITIONS = np.array([
    [0.00, 0.00],
    [0.08, 0.00],
    [0.03, 0.04],
], dtype=float)

NUM_MICS = len(MIC_POSITIONS)

LOOK_DIRECTION_DEG = 0.0
INTERFERER_DIRECTION_DEG = 90.0

# Angular resolution: 0.25 degrees.
ANGLES_DEG = np.linspace(-180.0, 180.0, 1441)

DB_FLOOR = -40.0

# Numerical threshold used to identify genuine deep nulls.
# This is our analysis criterion, not a paper-derived parameter.
NULL_THRESHOLD_DB = -35.0

PLOT_FILE = "task8_beampatterns.png"
CSV_FILE = "task8_beampattern_metrics.csv"


# Direction Vectors

def unit_vector(angle_deg):
    """Return the 2-D unit vector for an arrival direction."""
    angle = np.deg2rad(angle_deg)
    return np.array([
        np.cos(angle),
        np.sin(angle)
    ])


# Beamformer

def calculate_response(frequency_hz, angles_deg):
    """
    Calculate the normalized narrowband array response:

        B(f, theta) =
          (1/M) * abs(sum_m exp(j*k*r_m dot
                                   [u(theta)-u(theta0)]))

    where:
        M          = number of microphones
        r_m        = microphone position relative to centroid
        u(theta)   = arrival-direction unit vector
        theta0     = steering direction
        k          = 2*pi*f/c

    Uses equal microphone weights and ideal far-field propagation.

    Returns:
        response     : normalized linear magnitude
        response_db  : normalized magnitude in dB
    """

    # Centre microphone coordinates.
    positions = MIC_POSITIONS - MIC_POSITIONS.mean(axis=0)

    k = 2.0 * np.pi * frequency_hz / C
    target_vector = unit_vector(LOOK_DIRECTION_DEG)

    response = np.zeros(len(angles_deg), dtype=float)

    for i, angle_deg in enumerate(angles_deg):
        arrival_vector = unit_vector(angle_deg)

        phase = k * (
            positions @ (arrival_vector - target_vector)
        )

        complex_response = np.mean(np.exp(1j * phase))
        response[i] = abs(complex_response)

    response_db = 20.0 * np.log10(
        np.maximum(response, 1e-12)
    )

    return response, response_db



def interpolate_crossing(x1, y1, x2, y2, target_y):
    """Linearly estimate the x-coordinate of a threshold crossing."""

    if y2 == y1:
        return 0.5 * (x1 + x2)

    return x1 + (
        (target_y - y1) * (x2 - x1) / (y2 - y1)
    )



def first_null_bounds(angles_deg, response_db, target_index):
    """
    Find the nearest local minima on either side of the target
    that also reach NULL_THRESHOLD_DB or lower.

    A shallow local minimum is not automatically treated as a null.
    If no qualifying null exists on a side, that boundary is NaN.
    """

    left_candidates = []
    right_candidates = []

    # Search left of the target.
    for i in range(1, target_index):
        is_local_minimum = (
            response_db[i] <= response_db[i - 1]
            and response_db[i] <= response_db[i + 1]
        )

        is_deep_enough = response_db[i] <= NULL_THRESHOLD_DB

        if is_local_minimum and is_deep_enough:
            left_candidates.append(i)

    # Search right of the target.
    for i in range(target_index + 1, len(response_db) - 1):
        is_local_minimum = (
            response_db[i] <= response_db[i - 1]
            and response_db[i] <= response_db[i + 1]
        )

        is_deep_enough = response_db[i] <= NULL_THRESHOLD_DB

        if is_local_minimum and is_deep_enough:
            right_candidates.append(i)

    left = left_candidates[-1] if left_candidates else None
    right = right_candidates[0] if right_candidates else None

    left_angle = (
        angles_deg[left] if left is not None else np.nan
    )

    right_angle = (
        angles_deg[right] if right is not None else np.nan
    )

    return left, right, left_angle, right_angle


def calculate_metrics(frequency_hz, angles_deg, response, response_db):
    """
    Calculate:
      - Wavelength
      - 8 cm / wavelength ratio
      - Response at the target and 90-degree interferer
      - Interferer attenuation relative to the target
      - Approximate -3 dB beamwidth
      - Deepest qualifying null
      - Strongest response outside the first-null region

    Null and sidelobe metrics depend on the numerical definitions
    used here and should be interpreted accordingly.
    """

    target_index = int(
        np.argmin(np.abs(angles_deg - LOOK_DIRECTION_DEG))
    )

    interferer_index = int(
        np.argmin(
            np.abs(angles_deg - INTERFERER_DIRECTION_DEG)
        )
    )

    target_db = response_db[target_index]
    interferer_db = response_db[interferer_index]

    # Relative attenuation compared with the target direction.
    attenuation_db = target_db - interferer_db

    # Approximate -3 dB beamwidth
   

    threshold_db = target_db - 3.0

    left_crossing = np.nan
    right_crossing = np.nan

    # Find the nearest crossing to the left.
    for i in range(target_index - 1, -1, -1):
        if response_db[i] <= threshold_db < response_db[i + 1]:
            left_crossing = interpolate_crossing(
                angles_deg[i],
                response_db[i],
                angles_deg[i + 1],
                response_db[i + 1],
                threshold_db
            )
            break

    # Find the nearest crossing to the right.
    for i in range(target_index, len(response_db) - 1):
        if response_db[i] > threshold_db >= response_db[i + 1]:
            right_crossing = interpolate_crossing(
                angles_deg[i],
                response_db[i],
                angles_deg[i + 1],
                response_db[i + 1],
                threshold_db
            )
            break

    if (
        np.isfinite(left_crossing)
        and np.isfinite(right_crossing)
    ):
        beamwidth_3db = right_crossing - left_crossing
    else:
        beamwidth_3db = np.nan

 

    left_null, right_null, left_angle, right_angle = (
        first_null_bounds(
            angles_deg,
            response_db,
            target_index
        )
    )

    # Exclude a small neighbourhood around the target.
    null_mask = (
        (response_db <= NULL_THRESHOLD_DB)
        & (
            np.abs(angles_deg - LOOK_DIRECTION_DEG) > 2.0
        )
    )

    if np.any(null_mask):
        null_indices = np.where(null_mask)[0]

        null_index = null_indices[
            np.argmin(response_db[null_indices])
        ]

        deepest_null_angle = angles_deg[null_index]
        deepest_null_db = response_db[null_index]
    else:
        deepest_null_angle = np.nan
        deepest_null_db = np.nan


    if left_null is not None and right_null is not None:
        # Exclude the first-null-to-first-null main-lobe region.
        outside_main_lobe = np.ones(
            len(angles_deg), dtype=bool
        )

        outside_main_lobe[
            left_null:right_null + 1
        ] = False

        # Do not count the exact target sample as a sidelobe.
        outside_main_lobe[target_index] = False

        candidate_response = np.where(
            outside_main_lobe,
            response_db,
            -np.inf
        )

        sidelobe_index = int(np.argmax(candidate_response))

        sidelobe_angle = angles_deg[sidelobe_index]
        sidelobe_db = response_db[sidelobe_index]

    else:
        # Without both first-null boundaries, a defensible
        # main-lobe/sidelobe separation cannot be guaranteed.
        sidelobe_angle = np.nan
        sidelobe_db = np.nan

    return {
        "frequency_hz": frequency_hz,
        "wavelength_m": C / frequency_hz,
        "spacing_8cm_over_wavelength": (
            0.08 / (C / frequency_hz)
        ),
        "target_response_db": target_db,
        "interferer_90deg_response_db": interferer_db,
        "interferer_attenuation_db": attenuation_db,
        "approx_3db_beamwidth_deg": beamwidth_3db,
        "first_null_left_deg": left_angle,
        "first_null_right_deg": right_angle,
        "deepest_null_angle_deg": deepest_null_angle,
        "deepest_null_db": deepest_null_db,
        "strongest_sidelobe_angle_deg": sidelobe_angle,
        "strongest_sidelobe_db": sidelobe_db,
    }


def main():

    print("\nPART 8: THREE-MICROPHONE BEAM PATTERN ANALYSIS")
    print("=" * 92)

    print(f"Number of microphones: {NUM_MICS}")
    print(f"Steering direction: {LOOK_DIRECTION_DEG:.1f} degrees")
    print(
        f"Interferer direction: "
        f"{INTERFERER_DIRECTION_DEG:.1f} degrees"
    )
    print(
        "Model: ideal far-field, narrowband, "
        "equal-weight delay-and-sum"
    )
    print()

    all_metrics = []
    all_patterns = {}

    # Calculate responses for each frequency.
    for frequency in FREQUENCIES:

        response, response_db = calculate_response(
            frequency,
            ANGLES_DEG
        )

        metrics = calculate_metrics(
            frequency,
            ANGLES_DEG,
            response,
            response_db
        )

        all_metrics.append(metrics)
        all_patterns[frequency] = response_db



    
    # Save data to CSV

    with open(
        CSV_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=list(all_metrics[0].keys())
        )

        writer.writeheader()
        writer.writerows(all_metrics)

    print(f"\nMetrics saved to: {CSV_FILE}")

    # Plot

    fig, axes = plt.subplots(
        2,
        2,
        figsize=(13, 9),
        sharex=True,
        sharey=True
    )

    axes = axes.ravel()

    for ax, frequency, metrics in zip(
        axes,
        FREQUENCIES,
        all_metrics
    ):

        response_db = all_patterns[frequency]

        ax.plot(
            ANGLES_DEG,
            np.maximum(response_db, DB_FLOOR),
            linewidth=1.8,
            label="Array response"
        )

        ax.axvline(
            LOOK_DIRECTION_DEG,
            linestyle="--",
            linewidth=1.2,
            label="Target: 0 deg"
        )

        ax.axvline(
            INTERFERER_DIRECTION_DEG,
            linestyle=":",
            linewidth=1.5,
            label="Interferer: 90 deg"
        )

        interference_db = (
            metrics["interferer_90deg_response_db"]
        )

        ax.plot(
            INTERFERER_DIRECTION_DEG,
            max(interference_db, DB_FLOOR),
            marker="o"
        )

        ax.set_title(f"{frequency} Hz")
        ax.set_xlim(-180, 180)
        ax.set_ylim(DB_FLOOR, 2)

        ax.set_xticks(
            np.arange(-180, 181, 45)
        )

        ax.set_xlabel("Arrival direction (degrees)")
        ax.set_ylabel("Normalized response (dB)")
        ax.grid(True, alpha=0.3)

    axes[0].legend(
        fontsize=8,
        loc="lower left"
    )

    fig.suptitle(
        "Three-Microphone Delay-and-Sum Beam Patterns\n"
        "Target at 0 degrees; interferer at 90 degrees",
        fontsize=14
    )

    fig.tight_layout()

    fig.savefig(
        PLOT_FILE,
        dpi=200,
        bbox_inches="tight"
    )

    plt.show()

    print(f"Beam-pattern plot saved to: {PLOT_FILE}")
    print("\nFinished successfully.")


if __name__ == "__main__":
    main()
