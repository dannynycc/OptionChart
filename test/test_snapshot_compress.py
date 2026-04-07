"""
test_snapshot_compress.py
驗證快照 table 行列轉置（columnar）的壓縮效果與無損還原。
"""

import json
import os
import sys

SNAP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshots')


def rows_to_cols(table: list[dict]) -> dict[str, list]:
    """行列轉置：list-of-dicts → dict-of-lists"""
    if not table:
        return {}
    keys = list(table[0].keys())
    return {k: [row[k] for row in table] for k in keys}


def cols_to_rows(table_cols: dict[str, list]) -> list[dict]:
    """反轉置：dict-of-lists → list-of-dicts"""
    if not table_cols:
        return []
    keys = list(table_cols.keys())
    n = len(table_cols[keys[0]])
    return [{k: table_cols[k][i] for k in keys} for i in range(n)]


def test_roundtrip(snap_path: str):
    """驗證 rows → cols → rows 完全無損"""
    with open(snap_path, 'r', encoding='utf-8') as f:
        snap = json.load(f)

    table = snap.get('table')
    if not table or not isinstance(table, list):
        print(f"  SKIP (no table): {os.path.basename(snap_path)}")
        return True

    # 轉成 columnar
    cols = rows_to_cols(table)
    # 轉回 row
    restored = cols_to_rows(cols)

    # 逐行逐欄比對
    if len(table) != len(restored):
        print(f"  FAIL: row count {len(table)} vs {len(restored)}")
        return False

    for i, (orig, rest) in enumerate(zip(table, restored)):
        if orig != rest:
            # 找出差異
            for k in orig:
                if orig[k] != rest.get(k):
                    print(f"  FAIL row {i} key {k}: {orig[k]!r} vs {rest.get(k)!r}")
            return False

    # 大小比較
    orig_size = len(json.dumps(snap, ensure_ascii=False, separators=(',', ':')))
    snap_compressed = dict(snap)
    snap_compressed['table'] = cols
    comp_size = len(json.dumps(snap_compressed, ensure_ascii=False, separators=(',', ':')))
    saving = orig_size - comp_size
    pct = saving / orig_size * 100

    fname = os.path.basename(snap_path)
    print(f"  PASS  {fname}")
    print(f"        {orig_size:,} → {comp_size:,} bytes ({saving:,} saved, -{pct:.0f}%)")
    print(f"        table rows={len(table)}, cols={len(cols)}")
    return True


def main():
    print("=== 快照 columnar 壓縮測試 ===\n")

    all_ok = True
    total_orig = 0
    total_comp = 0

    for fname in sorted(os.listdir(SNAP_DIR)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(SNAP_DIR, fname)
        with open(path, 'r', encoding='utf-8') as f:
            snap = json.load(f)
        table = snap.get('table')
        if not table or not isinstance(table, list):
            continue

        ok = test_roundtrip(path)
        if not ok:
            all_ok = False

        # 累計
        orig_size = len(json.dumps(snap, ensure_ascii=False, separators=(',', ':')))
        snap_c = dict(snap)
        snap_c['table'] = rows_to_cols(table)
        comp_size = len(json.dumps(snap_c, ensure_ascii=False, separators=(',', ':')))
        total_orig += orig_size
        total_comp += comp_size

    print(f"\n{'='*50}")
    if total_orig > 0:
        print(f"全部快照合計: {total_orig:,} → {total_comp:,} bytes")
        print(f"節省: {total_orig - total_comp:,} bytes (-{(total_orig-total_comp)/total_orig*100:.0f}%)")
    print(f"結果: {'PASS' if all_ok else 'FAIL'}")
    return all_ok


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
