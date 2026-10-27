"""Reproducible compute-only benchmark; generated arrays, no customer data or I/O.

Run in a fresh process before/after a change. Defaults approximate one production
file; --parts/--tests/--sites/--batch-parts control the workload. JSON stdout.
"""
import argparse
import json
import os
from time import perf_counter

if os.environ.get('DEEPYIELD_BENCHMARK_BASELINE'):
    import deepyield.compute
    deepyield.compute.__path__ = [os.environ['DEEPYIELD_BENCHMARK_BASELINE'], *deepyield.compute.__path__]

import numpy as np
import pandas as pd
import pyarrow as pa

from deepyield.compute.release_compact import CalculationStore
from deepyield.compute.release_event_store import EventStore, PartEvents, retained_data_bytes
from deepyield.compute.release_stdf import CALCULATION_SCHEMA_VERSION
from deepyield.compute.release_control import Operation, operation
from deepyield.compute.release_performance_log import resources
from deepyield.compute.release_references import compact_sample_refs
from deepyield.compute.release_run_cache import computation
from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.compute.release_cgm import build_release_cgm_report
from deepyield.compute.release_mmsa import build_release_mmsa_report
from deepyield.compute.release_outliers import build_release_outlier_report
from deepyield.rules.loader import load_analysis_rules


def generated(parts, tests, sites, batch_parts, conditions=1):
    definitions = [dict(column=f'T_{i}', test_num=i, name=f'Test {i}',
        record_type='PTR', result_index=None, unit='V', low_limit=0., high_limit=100.)
        for i in range(tests)]
    schema = pa.struct([('column', pa.string()), ('test_num', pa.int64()),
        ('name', pa.string()), ('record_type', pa.string()), ('result_index', pa.int64()),
        ('unit', pa.string()), ('low_limit', pa.float64()), ('high_limit', pa.float64())])
    shared = EventStore(pa.array(definitions, type=schema))
    rows = []
    rng = np.random.default_rng(127)
    for first in range(0, parts, batch_parts):
        stop = min(parts, first + batch_parts)
        amount = (stop - first) * tests
        store = CalculationStore.__new__(CalculationStore)
        store.columns = {
            'result': ('float', rng.normal(50., 4., amount), np.ones(amount, dtype=bool)),
            'record_sequence': ('int', np.arange(first * tests, stop * tests, dtype=np.uint64), None),
            'test_flag': ('int', np.zeros(amount, dtype=np.uint8), None),
            'param_flag': ('int', np.zeros(amount, dtype=np.uint8), None),
            'result_valid': ('bool', np.ones(amount, dtype=bool), None)}
        store.definition_codes = np.tile(np.arange(tests, dtype=np.uint32), stop - first)
        store.bind_definitions(shared)
        store.last_per_test = True
        for part in range(first, stop):
            start = (part - first) * tests
            rows.append(dict(__part_index=part, SITE=part % sites, SBIN=1, PART_FLAG=0,
                HEAD_NUM=1, LOT_ID='L', WAFER_ID='W', DIE_X=part % 80, DIE_Y=part // 80,
                __source_record_sequence=part, STDF_PART_ID=str(part),
                __conditions_json=json.dumps({'test_temperature': '25', **({'variant':
                    min(conditions - 1, part * conditions // parts)} if conditions > 1 else {})}),
                __test_records=PartEvents(store, start, start + tests)))
    return pd.DataFrame(rows), dict(schema_version=CALCULATION_SCHEMA_VERSION,
        source_file='generated.stdf', source_sha256='benchmark', test_catalog=definitions)


@computation
def calculate(frame, metadata):
    catalog = load_analysis_rules()
    cpk = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
    cgm = build_release_cgm_report(frame, metadata, catalog=catalog, bin1_cpk_report=cpk)
    mmsa = build_release_mmsa_report(frame, metadata, catalog=catalog,
        bin1_cpk_report=cpk, cgm_report=cgm)
    return cpk, cgm, mmsa


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--parts', type=int, default=3289)
    parser.add_argument('--tests', type=int, default=3713)
    parser.add_argument('--sites', type=int, default=16)
    parser.add_argument('--batch-parts', type=int, default=32)
    parser.add_argument('--conditions', type=int, default=1)
    args = parser.parse_args()
    frame, metadata = generated(args.parts, args.tests, args.sites, args.batch_parts, args.conditions)
    result = dict(workload=vars(args), events=args.parts * args.tests,
                  retained_bytes=retained_data_bytes(frame), start=resources())
    start = perf_counter()
    with operation(Operation(name='benchmark_checks')), compact_sample_refs():
        reports = calculate(frame, metadata)
    result['checks_seconds'] = perf_counter() - start
    result['after_checks'] = resources()
    start = perf_counter()
    with operation(Operation(name='benchmark_outlier')):
        outlier = build_release_outlier_report(frame, metadata)
    result['outlier_seconds'] = perf_counter() - start
    result['after_outlier'] = resources()
    result['summary'] = dict(modules=[r['summary'] for r in reports], outlier=outlier['summary'])
    print(json.dumps(result))


if __name__ == '__main__':
    main()
