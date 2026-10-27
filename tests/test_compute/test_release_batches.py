"""Verify bounded arrays remain separate through IPC, Parquet and event views."""
import json

import pyarrow as pa
import pyarrow.parquet as pq

from deepyield.compute.release_stdf import _save, METADATA_KEY, SCHEMA_VERSION, read_release_wide_all


def test_parquet_preserves_native_batch_boundaries_and_event_offsets(tmp_path):
    event_type = pa.struct([('column', pa.string()), ('record_sequence', pa.int64()),
                            ('result', pa.float64())])
    schema = pa.schema([('__part_index', pa.int64()), ('SITE', pa.int64()),
        ('__test_records', pa.list_(event_type))], metadata={METADATA_KEY: json.dumps({
            'schema_version': SCHEMA_VERSION, 'test_catalog': [], 'row_count': 4}).encode()})
    batches = []
    for i in range(4):
        event = {'column': 'TEST', 'record_sequence': 10 + i, 'result': float(i)}
        batches.append(pa.RecordBatch.from_pylist([{'__part_index': i, 'SITE': i % 2,
            '__test_records': [event, {**event, 'record_sequence': 20 + i}]}], schema=schema))
    table = pa.Table.from_batches(batches)
    # IPC is the actual native/Python boundary; retain its chunk layout.
    output = pa.BufferOutputStream()
    with pa.ipc.new_stream(output, schema) as writer:
        writer.write_table(table)
    table = pa.ipc.open_stream(output.getvalue()).read_all()
    path = tmp_path / 'batches.parquet'
    _save(table, path)
    assert pq.ParquetFile(path).metadata.num_row_groups == 4
    assert pq.read_table(path).equals(table)
    frame, metadata = read_release_wide_all(path)
    assert metadata['row_count'] == 4
    for i, events in enumerate(frame['__test_records']):
        assert [event['result'] for event in events] == [float(i)] * 2
        assert [event['record_sequence'] for event in events] == [10 + i, 20 + i]
