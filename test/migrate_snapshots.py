"""
migrate_snapshots.py
將既有快照的 table 從 row 格式（list-of-dicts）轉為 columnar 格式（dict-of-lists）。
同時改用 compact JSON（無多餘空格）。
"""

import json
import os
import sys

SNAP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshots')


def rows_to_cols(table):
    if not table:
        return {}
    keys = list(table[0].keys())
    return {k: [row[k] for row in table] for k in keys}


def migrate():
    converted = 0
    skipped = 0

    for fname in sorted(os.listdir(SNAP_DIR)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(SNAP_DIR, fname)
        with open(path, 'r', encoding='utf-8') as f:
            snap = json.load(f)

        table = snap.get('table')
        if not table:
            skipped += 1
            continue

        if isinstance(table, dict):
            # 已經是 columnar，只需 compact JSON
            pass
        elif isinstance(table, list):
            snap['table'] = rows_to_cols(table)
        else:
            skipped += 1
            continue

        orig_size = os.path.getsize(path)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False, separators=(',', ':'))
        new_size = os.path.getsize(path)

        print(f"  {fname}: {orig_size:,} -> {new_size:,} (-{orig_size - new_size:,})")
        converted += 1

    print(f"\nConverted: {converted}, Skipped: {skipped}")


if __name__ == '__main__':
    migrate()
