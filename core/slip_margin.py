"""
"밀림 예상 범위" -- an expected print-to-cut registration slip allowance,
calculated from real measured data instead of picked from a textbook or a
single flat constant.

Why this exists: `core.cutline_core.MIN_GAP_MM` (2.0mm) is a single fixed
floor applied to every file, chosen from one prior review conversation, not
measured. The artist asked this round to actually CALCULATE an expected
slip range and fold it into the cutline margin, rather than keep leaning on
that flat number. The most honest data available for "how much does this
artist's own real production actually vary, point to point, on a line she
already trusts" is exactly what `core.margin_inspector` measures: the
per-sample-point spread (std) of a REAL, already-approved hand-drawn
cutline around REAL segmented artwork, on this same file.

Measured this round, on a real reference file, tile 0's 12
real cutline shapes (96 boundary samples each, core.margin_inspector):

    element   median_mm   std_mm
    elem0        1.78      0.76
    elem1        1.09      0.28
    elem2        1.25      0.28
    elem3        0.87      0.27
    elem4        1.95      0.46
    elem5        1.24      0.33
    elem6        1.28      0.36
    elem7        1.00      0.31
    elem8        1.03      0.57
    elem9        0.82      0.27
    elem10       0.66      0.26
    elem11       1.58      0.62

    median range: 0.66mm - 1.95mm (mean of medians 1.21mm)
    std:          mean 0.40mm, max 0.76mm

This std is NOT noise to average away -- it IS the real slip signal. A
hand-drawn cutline that sits at a near-constant distance from the artwork
everywhere would have std close to 0; the fact that even a single already-
printed, already-cut, already-approved line varies by ~0.3-0.8mm point to
point is direct physical evidence of how much this artist's actual
print/cut process (registration between the printer and the die/laser)
moves in practice -- exactly what "밀림" (slip) means here.

Method: treat the WORST observed per-element std among the measured
reference elements as the size of one slip "sigma", then add a safety
multiple of it on top of the artist's chosen base style margin (1.5mm),
instead of the flat MIN_GAP_MM=2.0 floor.

`safety_factor` was originally set to 2.0 (added allowance = double the
worst single-element wobble observed). Direct artist feedback on the first
full-sheet result built with that value: the resulting line sat visibly too
far outside her real hand-drawn line, and on top of that a large margin
buffer amplifies small edge noise (a stairstep pixel or two) into a visible
point sticking out of the line -- both read as "this cutline is too
bulky/pointy, pull it in". `safety_factor` is now **1.0** (added allowance
= the worst observed wobble, not double it) -- still a real, measured
number, just no longer doubled on top. If a future file's own measured std
comes back needing more room, raise this again with that file's own
numbers as the justification, not by default.

This is offered as a data-grounded STARTING point, not a claim of
statistical certainty -- 12 elements from one file is not enough to fit a
real distribution. It should keep being cross-checked against
`inspect_cutline_margins.py` on future files/vendors, and revised if a new
file's own std comes in higher than what informed this number.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_SAFETY_FACTOR = 1.0

# This round's reference measurement (a real reference file, tile 0, 12
# real elements) -- see module docstring for the full per-element table.
REFERENCE_STD_MM = [0.76, 0.28, 0.28, 0.27, 0.46, 0.33, 0.36, 0.31, 0.57, 0.27, 0.26, 0.62]


@dataclass
class SlipEstimate:
    base_margin_mm: float
    slip_allowance_mm: float
    total_margin_mm: float
    worst_std_mm: float
    safety_factor: float
    n_reference_elements: int


def estimate_slip_margin_mm(
    reference_std_mm: list = None,
    base_margin_mm: float = 1.5,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
) -> SlipEstimate:
    """
    `reference_std_mm`: per-element std_mm values from real, already-cut
    reference cutlines (e.g. `MarginStats.std_mm` for each of
    `core.margin_inspector.measure_margin_from_image`'s results on a real
    file/vendor). Defaults to this round's own 12-element measurement on
    a real reference file when the caller has nothing more specific for the
    file at hand.

    `slip_allowance_mm = safety_factor * worst(reference_std_mm)`, added on
    top of `base_margin_mm` (the artist's own 1.5mm style standard) to get
    `total_margin_mm` -- the number to actually pass as `margin_mm` into
    the cutline generators for this file.
    """
    stds = reference_std_mm if reference_std_mm else REFERENCE_STD_MM
    if not stds:
        raise ValueError("reference_std_mm must have at least one measurement")

    worst_std = max(stds)
    slip_allowance = safety_factor * worst_std
    return SlipEstimate(
        base_margin_mm=base_margin_mm,
        slip_allowance_mm=round(slip_allowance, 3),
        total_margin_mm=round(base_margin_mm + slip_allowance, 3),
        worst_std_mm=worst_std,
        safety_factor=safety_factor,
        n_reference_elements=len(stds),
    )
