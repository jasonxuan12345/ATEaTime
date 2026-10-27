"""Adjacent moving ranges for CGM PDF diagnostics; never change release decisions."""
import numpy as np

from deepyield.compute.release_numeric import INFINITE_CG, CG_TOLERANCE_FRACTION
from deepyield.rules.conditions import as_float

D2 = 1.128
D4 = 3.267
TOLERANCE_FRACTION = CG_TOLERANCE_FRACTION


def moving_range_metrics(values, tolerance):
    tolerance = as_float(tolerance)
    if tolerance is None or tolerance <= 0:
        raise ValueError('MR 需要有效的正公差')
    data = np.asarray(values, dtype=float)
    if data.ndim != 1 or not np.isfinite(data).all():
        raise ValueError('MR 需要有限的一维测量序列')
    limit = D2 * TOLERANCE_FRACTION * tolerance / (6 * 1.33)
    ranges = np.full(data.size, np.nan)
    average = sigma = cg = None
    if data.size >= 2:
        with np.errstate(over='ignore', invalid='ignore'):
            ranges[1:] = np.abs(np.diff(data))
            average = float(ranges[1:].mean())
        if not np.isfinite(ranges[1:]).all() or not np.isfinite(average):
            raise ValueError('MR 相邻差分超出有效数值范围')
        sigma = average / D2
        cg = INFINITE_CG if average == 0 else TOLERANCE_FRACTION * tolerance / (6 * sigma)
    return {'values': ranges, 'average': average, 'sigma': sigma, 'cg': cg,
            'target_limit': limit, 'spike_ucl': D4 * limit,
            'spike_count': int(np.count_nonzero(ranges > D4 * limit))}
