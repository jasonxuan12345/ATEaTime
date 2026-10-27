"""Simulated production STDF -> native parser -> Bin1 CPK -> five-panel PDF."""
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.generate_cgm_pdf_demo import _record, _cn
from deepyield.compute.release_stdf import import_release_stdf
from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.compute.release_cpk_pdf import render_cpk_pdf, cpk_population
from deepyield.compute.release_chart_samples import report_samples
from deepyield.rules.loader import load_analysis_rules


def demo_stdf(site_count, *, wafer=False, paginate=False):
    rng = np.random.default_rng(20261020)
    mir = struct.pack('<IIBcccHc', 0, 0, 1, b'P', b' ', b' ', 0, b' ')
    fields = ['CPK_DEMO_LOT', f'CpkDemo{site_count}S', 'DEMO_TESTER', 'Simulated', 'CPK_DEMO', '1', '',
              'DEMO', '', '', 'FT', '25', 'SIMULATED DATA - FOR LAYOUT REVIEW ONLY']
    payload = bytearray(_record(0, 10, bytes([2, 4])) + _record(1, 10, mir + b''.join(_cn(x) for x in fields)))
    if wafer:
        payload += _record(2, 10, struct.pack('<BBI', 1, 1, 0) + _cn('W_DEMO_01'))
    tests = [(1001 + i, f'Process_capability_{i + 1:02d}', .85 + i * .025) for i in range(13 if paginate else 1)]
    tests += [(2001, 'Borderline_process', 1.78), (3001, 'Stable_process', 2.6),
              (4001, 'Digital_status_check', None)]
    runs = 100
    coordinates = None
    if wafer:
        grid = [(x + 20, y + 20) for y in range(-19, 20) for x in range(-19, 20) if x*x + y*y <= 19*19]
        chosen = rng.permutation(len(grid))[:runs * site_count]
        coordinates = [grid[index] for index in chosen]
    wave = rng.normal(size=(len(tests), runs))
    # First 90 are Bin1, last 10 are non-Bin1. Normalize actual Bin1 population.
    wave[:, :90] -= wave[:, :90].mean(axis=1, keepdims=True)
    wave[:, :90] /= wave[:, :90].std(axis=1, ddof=1, keepdims=True)
    for run in range(runs):
        for site in range(1, site_count + 1):
            payload += _record(5, 10, bytes([1, site]))
            for i, (number, name, target) in enumerate(tests):
                low, high = (1., 1.) if target is None else (0., 10.)
                offset = .005 if number == 2001 else .075
                value = 1. if target is None else 5 + wave[i, run] * 10 / (6 * (target + (site - 1) * offset))
                ptr = struct.pack('<IBBBBf', number, 1, site, 0, 0, value) + _cn(name) + _cn('')
                ptr += struct.pack('<Bbbbff', 0, 0, 0, 0, low, high) + _cn('V')
                payload += _record(15, 10, ptr)
            bin_number = 1 if run < 90 else 2
            # Unique integer die positions; -32768 marks absent coordinates.
            k = run * site_count + site - 1
            x, y = coordinates[k] if coordinates else (-32768, -32768)
            prr = struct.pack('<BBBHHHhhI', 1, site, 0, len(tests), bin_number, bin_number, x, y, 0)
            payload += _record(5, 20, prr + _cn(f'R{run + 1}_S{site}') + _cn('') + b'\0')
    return bytes(payload)


def generate(output_dir, site_counts=(1, 4, 8, 16)):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_analysis_rules()
    for count in site_counts:
        source = output_dir / f'CpkDemo{count}S.stdf'
        if source.exists():
            raise FileExistsError('示例输入已存在，请指定新的输出目录')
        source.write_bytes(demo_stdf(count, wafer=count == 8, paginate=count == 16))
        frame, metadata = import_release_stdf(source)
        report = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
        result = render_cpk_pdf(report, metadata=metadata, population=cpk_population(frame, report),
            sample_provider=lambda item: report_samples(frame, item, metadata['source_sha256']))
        destination = output_dir / result['filename']
        with destination.open('xb') as stream:
            stream.write(result['content'])
        print(f'{destination.resolve()} | evaluated={result["evaluated"]}, actions={result["action_count"]}, pages={result["page_count"]}, panels={set(result["detail_modes"])}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='output/pdf/cpk-demo')
    parser.add_argument('--sites', type=int, nargs='+', default=[1, 4, 8, 16])
    args = parser.parse_args()
    generate(args.output_dir, args.sites)
