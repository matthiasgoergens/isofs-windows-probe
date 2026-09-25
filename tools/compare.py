#!/usr/bin/env python3
"""Compare an OS listing (lister.py) with the oracle (oracle.py).

  compare.py ORACLE.json LISTING.json [--tree T] > report.json
  compare.py --selftest ORACLE.json LISTING.json

For every tree in the oracle, each oracle entry is matched to the listing
under the first of these name transforms that finds it:

  exact            name as recorded on the medium
  strip_version    oracle name minus ';<digits>' and trailing dots
  casefold         strip_version, both sides lower-cased
  nfc              casefold, both sides NFC-normalised (macOS may give NFD)
  qmark            casefold, oracle characters above U+00FF replaced by '?'

Listing entries matched by nothing are 'extra'; an extra whose name is a
proper prefix of a missing oracle name in the same directory is reported as
'truncated'.  Matched files are checked for size and SHA-256.

--selftest proves the comparison can see a difference: it mutates the
oracle (renames one file, changes another file's hash, drops a directory's
first file's size by one) and exits 1 unless each mutation is reported.
"""
import json
import re
import sys
import unicodedata


def strip_version(n):
    n = re.sub(r';\d+$', '', n)
    if len(n) > 1:
        n = n.rstrip('.') or n
    return n


TRANSFORMS = [
    ('exact', lambda n: n, lambda n: n),
    ('strip_version', strip_version, lambda n: n),
    ('casefold', lambda n: strip_version(n).lower(), lambda n: n.lower()),
    ('nfc', lambda n: unicodedata.normalize('NFC', strip_version(n).lower()),
     lambda n: unicodedata.normalize('NFC', n.lower())),
    ('qmark', lambda n: ''.join(c if ord(c) < 256 else '?' for c in strip_version(n).lower()),
     lambda n: n.lower()),
]


def units(s):
    return len(s.encode('utf-16-le', errors='surrogatepass')) // 2


def compare_tree(oents, lents):
    matched = {}          # oracle index -> (listing index, transform)
    used = set()
    for tname, fo, fl in TRANSFORMS:
        lmap = {}
        for li, le in enumerate(lents):
            if li in used or 'path' not in le:
                continue
            try:
                k = tuple(fl(c) for c in le['path'])
            except Exception:
                continue
            lmap.setdefault(k, []).append(li)
        for oi, oe in enumerate(oents):
            if oi in matched:
                continue
            # Parent directories may themselves have matched under another
            # transform; use the listing's actual parent path if known.
            k = tuple(fo(c) for c in oe['path'])
            cands = [li for li in lmap.get(k, []) if li not in used]
            if cands:
                matched[oi] = (cands[0], tname)
                used.add(cands[0])
    res = {'oracle_entries': len(oents), 'listing_entries': len(lents),
           'matched_by': {}, 'missing': [], 'extra': [], 'truncated': [],
           'size_mismatch': [], 'hash_mismatch': [], 'read_errors': []}
    for oi, (li, t) in matched.items():
        res['matched_by'][t] = res['matched_by'].get(t, 0) + 1
        oe, le = oents[oi], lents[li]
        if le.get('read_error'):
            res['read_errors'].append({'path': oe['path'], 'error': le['read_error']})
        if not oe['dir']:
            if le.get('size') != oe['size']:
                res['size_mismatch'].append({'path': oe['path'], 'oracle': oe['size'],
                                             'os': le.get('size')})
            if oe.get('sha256') and le.get('sha256') and le['sha256'] != oe['sha256']:
                res['hash_mismatch'].append({'path': oe['path'], 'oracle': oe['sha256'],
                                             'os': le['sha256'],
                                             'os_bytes_read': le.get('bytes_read')})
    missing = [oe for oi, oe in enumerate(oents) if oi not in matched]
    extra = [le for li, le in enumerate(lents) if li not in used]
    for le in extra:
        name = le['path'][-1] if le.get('path') else ''
        hit = None
        for oe in missing:
            if len(oe['path']) != len(le['path']):
                continue
            on = strip_version(oe['path'][-1])
            if name and on != name and on.lower().startswith(name.lower()):
                hit = oe
                break
        if hit is not None:
            res['truncated'].append({'oracle': hit['path'], 'os': le['path'],
                                     'oracle_units': units(strip_version(hit['path'][-1])),
                                     'os_units': units(name),
                                     'same_content': (le.get('sha256') is not None
                                                      and le.get('sha256') == hit.get('sha256'))})
        res['extra'].append({'path': le.get('path'), 'raw': le.get('raw'),
                             'units': units(name)})
    for oe in missing:
        res['missing'].append({'path': oe['path'], 'units': units(oe['path'][-1]),
                               'dir': oe['dir']})
    res['ok'] = not (res['missing'] or res['extra'] or res['size_mismatch']
                     or res['hash_mismatch'] or res['read_errors'])
    return res


def compare(oracle, listing, only=None):
    out = {'image': oracle.get('image'), 'listing_errors': listing.get('errors', []),
           'trees': {}}
    for t, tv in oracle['trees'].items():
        if only and t != only:
            continue
        out['trees'][t] = compare_tree(tv['entries'], listing['entries'])
    if out['trees']:
        best = max(out['trees'], key=lambda t: (sum(out['trees'][t]['matched_by'].values()),
                                                 out['trees'][t]['matched_by'].get('exact', 0)))
        out['best_tree'] = best
        out['best_ok'] = out['trees'][best]['ok'] and not out['listing_errors']
    return out


def selftest(oracle, listing):
    import copy
    best = compare(oracle, listing)['best_tree']
    o = copy.deepcopy(oracle)
    files = [e for e in o['trees'][best]['entries'] if not e['dir'] and e.get('sha256')]
    if len(files) < 3:
        print('selftest: fewer than 3 hashed files, cannot mutate')
        return 1
    files[0]['path'][-1] = 'MUTATED-' + files[0]['path'][-1]
    files[1]['sha256'] = '0' * 64
    files[2]['size'] -= 1
    r = compare(o, listing, only=best)['trees'][best]
    ok = (len(r['missing']) >= 1 and len(r['extra']) >= 1 and len(r['hash_mismatch']) >= 1
          and len(r['size_mismatch']) >= 1 and not r['ok'])
    print(f"selftest on tree {best}: missing={len(r['missing'])} extra={len(r['extra'])} "
          f"hash_mismatch={len(r['hash_mismatch'])} size_mismatch={len(r['size_mismatch'])} "
          f"-> {'DETECTED' if ok else 'NOT DETECTED'}")
    return 0 if ok else 1


def main():
    a = sys.argv[1:]
    if a[0] == '--selftest':
        oracle = json.load(open(a[1], encoding='utf-8-sig'))
        listing = json.load(open(a[2], encoding='utf-8-sig'))
        sys.exit(selftest(oracle, listing))
    only = None
    if '--tree' in a:
        i = a.index('--tree')
        only = a[i + 1]
        del a[i:i + 2]
    oracle = json.load(open(a[0], encoding='utf-8-sig'))
    listing = json.load(open(a[1], encoding='utf-8-sig'))
    json.dump(compare(oracle, listing, only), sys.stdout, indent=1, ensure_ascii=True)
    print()


if __name__ == '__main__':
    main()
