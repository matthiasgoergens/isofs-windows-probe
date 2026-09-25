#!/usr/bin/env python3
"""Check that the PowerShell (.NET) listing and the Python listing report
the same set of names, as raw UTF-16 code units, and the same sizes.

  pscheck.py LISTING.json PS-LISTING.json
"""
import json
import sys

a = json.load(open(sys.argv[1], encoding='utf-8-sig'))
b = json.load(open(sys.argv[2], encoding='utf-8-sig'))


def key_py(e):
    return tuple(c.encode('utf-16-be', errors='surrogatepass').hex() for c in e['path'])


def key_ps(e):
    comps = e['path_units']
    if comps and isinstance(comps[0], str):      # single component flattened
        comps = [comps]
    return tuple(''.join(u) for u in comps)


pa = {key_py(e): e.get('size') for e in a['entries']}
pb = {key_ps(e): e.get('size') for e in b['entries']}
only_a = sorted(set(pa) - set(pb))
only_b = sorted(set(pb) - set(pa))
size_diff = [k for k in set(pa) & set(pb) if pa[k] != pb[k]]
print(f'python={len(pa)} powershell={len(pb)} only_python={len(only_a)} '
      f'only_powershell={len(only_b)} size_diff={len(size_diff)} ps_error={b.get("error")!r}')
for k in only_a[:5] + only_b[:5]:
    print('  differs:', k)
sys.exit(0 if not (only_a or only_b or size_diff) else 1)
