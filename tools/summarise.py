#!/usr/bin/env python3
"""One line per image from a results directory written by ci/run-*.

  summarise.py RESULTS_DIR
"""
import json
import os
import sys

top = sys.argv[1]


def load(p):
    try:
        return json.load(open(p, encoding='utf-8-sig'))
    except Exception:
        return None


for name in sorted(os.listdir(top)):
    d = os.path.join(top, name)
    if not os.path.isdir(d):
        continue
    fs = ''
    v = load(os.path.join(d, 'volume.json'))
    if v:
        fs = f"fs={v.get('FileSystem') or v.get('FileSystemType')}"
    elif os.path.exists(os.path.join(d, 'fstype.txt')):
        fs = 'fs=' + open(os.path.join(d, 'fstype.txt')).read().strip()
    err = os.path.join(d, 'mount-error.txt')
    if os.path.exists(err):
        print(f'{name:32s} MOUNT-ERROR {fs} {open(err, encoding="utf-8-sig", errors="replace").read().strip()[:300]!r}')
        continue
    c = load(os.path.join(d, 'compare.json'))
    if not c:
        print(f'{name:32s} NO-COMPARISON {fs}')
        continue
    t = c['best_tree']
    r = c['trees'][t]
    extra = ''
    if r['truncated']:
        extra += ' truncated=' + ','.join(f"{x['oracle_units']}->{x['os_units']}" for x in r['truncated'])
    if r['missing']:
        extra += ' missing=' + ','.join(repr(x['path'][-1][:24]) for x in r['missing'][:6])
    if r['size_mismatch']:
        extra += ' size=' + ','.join(f"{x['oracle']}->{x['os']}" for x in r['size_mismatch'][:4])
    if r['read_errors']:
        extra += f" read_errors={len(r['read_errors'])}"
    if c['listing_errors']:
        extra += f" listing_errors={len(c['listing_errors'])}"
    print(f"{name:32s} {'OK ' if c['best_ok'] else 'DIFF'} {fs} tree={t} "
          f"matched={r['matched_by']} of {r['oracle_entries']} missing={len(r['missing'])} "
          f"extra={len(r['extra'])} hash_mismatch={len(r['hash_mismatch'])}{extra}")
