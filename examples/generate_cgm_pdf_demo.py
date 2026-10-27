"""Generate deterministic STDF -> parsed CGM -> PDF examples (simulated data)."""
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepyield.compute.release_stdf import import_release_stdf
from deepyield.compute.release_cgm import build_release_cgm_report
from deepyield.compute.release_cgm_pdf import render_cgm_pdf
from deepyield.compute.release_chart_samples import report_samples
from deepyield.rules.loader import load_analysis_rules


def _record(kind, sub, payload):
    return struct.pack('<HBB', len(payload), kind, sub) + payload


def _cn(text):
    encoded = text.encode('ascii')
    return bytes([len(encoded)]) + encoded


def demo_stdf(site_count, *, paginate=False):
    """Valid STDF V4 with MIR identity, 25 executions/site, PTR and PRR records."""
    device = f'DemoDevice{site_count}S'
    mir = struct.pack('<IIBcccHc', 0, 0, 1, b'P', b' ', b' ', 0, b' ')
    fields = ['DEMO_LOT', device, 'DEMO_TESTER', 'Simulated', 'CGM_DEMO', '1', '',
              'DEMO', '', '', 'FT', '25', 'SIMULATED DATA - FOR LAYOUT REVIEW ONLY']
    payload = bytearray(_record(0, 10, bytes([2, 4])) + _record(1, 10, mir + b''.join(_cn(x) for x in fields)))
    # Include finite Pass, all-Infinity, Bin1 exemption, state-bit exclusion.
    failures = 13 if paginate else 1
    tests = [(1001 + i, f'Repeatability_{i + 1:02d}', .65 + .015 * i) for i in range(failures)]
    tests += [(2001, 'Borderline_repeatability', 1.1), (3001, 'Stable_reference', 1.8),
              (3002, 'Zero_variation', None), (3003, 'Bin1_CPK_exemption', .5),
              (4001, 'Digital_status_check', None)]
    wave = np.sin(np.linspace(0, 4 * np.pi, 25))
    wave /= wave.std(ddof=1)
    for run in range(25):
        for site in range(1, site_count + 1):
            payload += _record(5, 10, bytes([1, site]))
            for number, name, cg in tests:
                low, high = (1., 1.) if number == 4001 else (0., 10.)
                value = 1. if number == 4001 else 5.
                if cg is not None:
                    value += wave[run] * 10 / (6 * (cg + (site - 1) * .07))
                if number == 3003:
                    value = 5 + (wave[run] * .02 if run < 15 else (4 if run % 2 else -4))
                ptr = struct.pack('<IBBBBf', number, 1, site, 0, 0, value) + _cn(name) + _cn('')
                ptr += struct.pack('<Bbbbff', 0, 0, 0, 0, low, high) + _cn('V')
                payload += _record(15, 10, ptr)
            bin_number = 1 if run < 15 else 2
            prr = struct.pack('<BBBHHHhhI', 1, site, 0, len(tests), bin_number, bin_number, 0, 0, 0)
            payload += _record(5, 20, prr + _cn(f'R{run + 1}_S{site}') + _cn('') + b'\0')
    return bytes(payload)


def generate(output_dir, site_counts=(1, 4, 8, 16)):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_analysis_rules()
    for count in site_counts:
        source = output_dir / f'DemoDevice{count}S.stdf'
        if source.exists():
            raise FileExistsError('示例输入已存在，请指定新的输出目录')
        source.write_bytes(demo_stdf(count, paginate=count == 16))
        frame, metadata = import_release_stdf(source)
        report = build_release_cgm_report(frame, metadata, catalog=catalog)
        result = render_cgm_pdf(report, metadata=metadata,
            sample_provider=lambda item: report_samples(frame, item, metadata['source_sha256']))
        destination = output_dir / result['filename']
        with destination.open('xb') as stream:
            stream.write(result['content'])
        print(f'{destination.resolve()} | evaluated={result["evaluated"]}, actions={result["action_count"]}, pages={result["page_count"]}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='output/pdf/cgm-demo')
    parser.add_argument('--sites', type=int, nargs='+', default=[1, 4, 8, 16])
    args = parser.parse_args()
    generate(args.output_dir, args.sites)
