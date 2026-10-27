"""Release numeric kernels on already validated, finite, grouped samples."""
import numpy as np
from deepyield.rules.conditions import as_float


INFINITE_CG = "Infinity"
CG_TOLERANCE_FRACTION = 0.20


def require_current_cg_policy(report):
    """Saved values need recalculation before use under the 20% convention."""
    if (report.get('policy') or {}).get('cg_tolerance_fraction') != CG_TOLERANCE_FRACTION:
        raise ValueError('Cg 计算口径已更新为 20% 公差，请重新计算 CGM 后再使用或导出')


def cg_number(value):
    """Use infinity for comparisons while keeping reports valid JSON."""
    return float('inf') if value == INFINITE_CG else as_float(value)


def cpk_metrics(values, low, high):
    data = np.asarray(values, dtype=np.float64)
    mean = float(data.mean()) if data.size else None
    std = float(data.std(ddof=1)) if data.size >= 2 else None
    valid = std is not None and np.isfinite(std) and std != 0
    candidates = []
    if valid and low is not None:
        candidates.append((mean - low) / (3 * std))
    if valid and high is not None:
        candidates.append((high - mean) / (3 * std))
    cpk = min(candidates) if candidates else None
    cp = (high - low) / (6 * std) if valid and low is not None and high is not None else None
    outside = np.zeros(data.size, dtype=bool)
    if low is not None:
        outside |= data < low
    if high is not None:
        outside |= data > high
    return {'Mean': mean, 'Std': std, 'cpk': cpk,
            'FailCount': int(outside.sum()) if low is not None or high is not None else None,
            'cpk/cp': cpk / cp if cp is not None and np.isfinite(cp) and cp != 0 else None}


def site_metrics(values, low, high):
    data = np.asarray(values, dtype=np.float64)
    std = float(data.std(ddof=1)) if data.size >= 2 else None
    cg = CG_TOLERANCE_FRACTION * (high - low) / (6 * std) if low is not None and high is not None and std is not None and np.isfinite(std) and std != 0 else None
    if std == 0 and low is not None and high is not None and low < high:
        cg = INFINITE_CG
    return std, cg
