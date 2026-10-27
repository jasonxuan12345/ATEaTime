"""Release wafer points using the existing coordinate quality/display rules."""
from __future__ import annotations

import json

from deepyield.compute.ews_yield.identity import classify_coordinate_status
from deepyield.compute.ews_yield.models import CoordinateStatus
from deepyield.compute.release_charts import thin_points
from deepyield.device_config import load_device_registry
from deepyield.device_config.wafer_map_display import apply_display_to_map_points, display_to_context


def release_wafer_data(points: list[dict], item: dict, *, limit: int = 5000) -> dict:
    part_type = (item.get('conditions') or {}).get('part_type')
    device = load_device_registry().find_by_part_type(str(part_type or ''))
    display = device.wafer_map.display if device and device.wafer_map else None
    positions: dict[tuple, list[dict]] = {}
    skipped = 0
    for point in points:
        if not point['wafer'] or not point['lot'] or classify_coordinate_status(point['x'], point['y']) != CoordinateStatus.USABLE:
            skipped += 1
            continue
        key = (point['lot'], point['wafer'], int(point['x']), int(point['y']))
        positions.setdefault(key, []).append(point)
    wafers: dict[tuple, list[dict]] = {}
    for (lot, wafer, x, y), samples in positions.items():
        latest = max(samples, key=lambda p: (p['record_sequence'], p['part_index']))
        wafers.setdefault((lot, wafer), []).append({**latest, 'x': x, 'y': y,
            'lot_id': lot, 'wafer_id': wafer, 'count': len(samples), 'raw_x': x, 'raw_y': y})
    groups = []
    for (lot, wafer), dies in sorted(wafers.items()):
        transformed = apply_display_to_map_points(dies, display)
        groups.append({'key': json.dumps([lot, wafer], ensure_ascii=False), 'lot': lot, 'wafer': wafer,
            'total_dies': len(dies), 'points': thin_points(transformed, limit),
            'bounds': {'minX': min(p['x'] for p in transformed), 'maxX': max(p['x'] for p in transformed),
                'minY': min(p['y'] for p in transformed), 'maxY': max(p['y'] for p in transformed)},
            'sampled': len(dies) > limit, 'duplicate_positions': sum(p['count'] > 1 for p in dies)})
    return {'wafers': groups, 'excluded_count': skipped,
            'value_range': [min(p['value'] for dies in wafers.values() for p in dies),
                            max(p['value'] for dies in wafers.values() for p in dies)] if groups else None,
            'display': display_to_context(display), 'display_note': '采用产品坐标配置' if display else '采用原始默认方向（无匹配产品显示配置）',
            'policy': '每个位置显示最后一条入选有效记录，点击查看该位置全部入选记录',
            'empty_reason': None if groups else '当前入选样本缺少有效 lot/wafer 身份或晶圆坐标'}
