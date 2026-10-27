"""Robust per-population PAT candidates, with explicit unevaluable states."""
import numpy as np


def robust_outlier_statistics_many(populations, sigma_multiplier, minimum_samples):
    """Exact linear percentiles, batched only for populations of equal length.

    Independent reference groups never share samples. Chunking bounds the
    temporary matrix (including scores) and preserves each input's order.
    """
    grouped, output = {}, [None] * len(populations)
    for index, values in enumerate(populations):
        grouped.setdefault(len(values), []).append(index)
    for amount, members in grouped.items():
        step = max(1, (16 * 1024**2) // max(1, amount * 32))
        for start in range(0, len(members), step):
            selected = members[start:start + step]
            if not amount or len(selected) == 1:
                for index in selected:
                    output[index] = robust_outlier_statistics(populations[index], sigma_multiplier, minimum_samples)
                continue
            data = np.stack([populations[index] for index in selected]).astype(np.float64, copy=False)
            with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
                q1, median, q3 = np.percentile(data, [25, 50, 75], axis=1, method='linear')
                sigma = (q3 - q1) / 1.35
                low, high = median - sigma_multiplier * sigma, median + sigma_multiplier * sigma
                scores = np.abs(data - median[:, None]) / sigma[:, None]
            finite = np.isfinite(np.stack([q1, median, q3, sigma, low, high])).all(axis=0)
            finite_scores = np.isfinite(scores).all(axis=1)
            for row, index in enumerate(selected):
                result = dict(n=amount, q1=None, median=None, q3=None, robust_sigma=None,
                    lower_limit=None, upper_limit=None, status='INSUFFICIENT_DATA',
                    reason='TOO_FEW_VALID_SAMPLES', candidate_count=0)
                hits = []
                if not finite[row]:
                    result.update(status='NOT_CALCULABLE', reason='NON_FINITE_STATISTICS')
                else:
                    result.update(q1=float(q1[row]), median=float(median[row]), q3=float(q3[row]),
                        robust_sigma=float(sigma[row]), lower_limit=float(low[row]), upper_limit=float(high[row]))
                    if amount < minimum_samples:
                        pass
                    elif sigma[row] <= 0:
                        result.update(status='NOT_CALCULABLE', reason='ZERO_ROBUST_SPREAD')
                    elif not finite_scores[row]:
                        result.update(status='NOT_CALCULABLE', reason='NON_FINITE_SCORES')
                    else:
                        found = np.flatnonzero(scores[row] > sigma_multiplier)
                        hits = [(int(position), float(scores[row, position])) for position in found]
                        result.update(status='OUTLIER_CANDIDATES' if hits else 'NO_OUTLIER_FOUND',
                                      reason=None, candidate_count=len(hits))
                output[index] = result, hits
    return output


def robust_outlier_statistics(values, sigma_multiplier, minimum_samples):
    """Return baseline evidence and (input position, score) for strict exceedances."""
    data = np.asarray(values, dtype=np.float64)
    result = dict(n=len(data), q1=None, median=None, q3=None, robust_sigma=None,
                  lower_limit=None, upper_limit=None, status='INSUFFICIENT_DATA',
                  reason='TOO_FEW_VALID_SAMPLES', candidate_count=0)
    if not data.size:
        return result, []
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        q1, median, q3 = np.percentile(data, [25, 50, 75], method='linear')
        sigma = (q3 - q1) / 1.35
        low, high = median - sigma_multiplier * sigma, median + sigma_multiplier * sigma
    if not np.isfinite([q1, median, q3, sigma, low, high]).all():
        result.update(status='NOT_CALCULABLE', reason='NON_FINITE_STATISTICS')
        return result, []
    result.update(q1=float(q1), median=float(median), q3=float(q3),
                  robust_sigma=float(sigma), lower_limit=float(low), upper_limit=float(high))
    if data.size < minimum_samples:
        return result, []
    if sigma <= 0:
        result.update(status='NOT_CALCULABLE', reason='ZERO_ROBUST_SPREAD')
        return result, []
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        scores = np.abs(data - median) / sigma
    if not np.isfinite(scores).all():
        result.update(status='NOT_CALCULABLE', reason='NON_FINITE_SCORES')
        return result, []
    indices = np.flatnonzero(scores > sigma_multiplier)
    hits = [(int(index), float(scores[index])) for index in indices]
    result.update(status='OUTLIER_CANDIDATES' if hits else 'NO_OUTLIER_FOUND',
                  reason=None, candidate_count=len(hits))
    return result, hits
