"""Reviewed cross-repository cold-start priors.

The table intentionally starts empty. Calibration evidence must earn a reviewed entry; local learned
snapshots remain available immediately and take precedence when present.
"""

from pebra.core.warm_prior import CalibratedPriorCell

CALIBRATED_PRIORS: tuple[CalibratedPriorCell, ...] = ()
