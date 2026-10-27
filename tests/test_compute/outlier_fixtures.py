"""Construct imported-frame equivalents entirely in memory."""
import json

import numpy as np
import pandas as pd
import pyarrow as pa

from deepyield.compute.release_event_store import EventStore, PartEvents
from deepyield.compute.release_stdf import SCHEMA_VERSION


def population(center=10., count=40):
    return list(center + np.linspace(-.2, .2, count))


def dataset(channels=None, test_count=1, compact=False):
    channels = channels or {(1, 1, 'W1'): population() + [12.], (1, 2, 'W1'): population(20.)}
    tests = [dict(column=f'TEST_{1001 + index}', test_num=1001 + index,
        name=f'TEST NAME {index}', record_type='PTR', result_index=None, unit='mA',
        low_limit=0., high_limit=100.) for index in range(test_count)]
    condition = json.dumps(dict(part_type='PRODUCT', job_name='PROGRAM', job_revision='A',
                               test_code='FT', test_temperature='25'))
    rows = []
    for (head, site, wafer), values in channels.items():
        for value in values:
            part = len(rows) + 1
            events = [dict(test, result=float(value), record_sequence=part * 10 + index,
                           test_flag=0, param_flag=0, result_valid=True) for index, test in enumerate(tests)]
            rows.append(dict(__part_index=part, __source_record_sequence=part * 10 + 9,
                Name='SAME_NAME', STDF_PART_ID='REPEATED_ID', LOT_ID='LOT', WAFER_ID=wafer,
                DIE_X=-1, DIE_Y=0, HEAD_NUM=head, SITE=site, SBIN=1, PART_FLAG=0,
                __conditions_json=condition, __test_records=events))
    frame = pd.DataFrame(rows)
    if compact:
        events = [event for row in rows for event in row['__test_records']]
        store = EventStore(pa.array(events))
        frame['__test_records'] = pd.Series(
            [PartEvents(store, i * test_count, (i + 1) * test_count) for i in range(len(rows))], dtype=object)
    metadata = dict(schema_version=SCHEMA_VERSION, source_file='generated.stdf',
                    source_sha256='generated-memory-source', test_catalog=tests)
    return frame, metadata
