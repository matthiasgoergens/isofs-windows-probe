#!/usr/bin/env python3
"""List a mounted file system as the OS presents it.

  lister.py MOUNTPOINT OUT.json [--subtree /A/B]

With --subtree only the directories on the way to /A/B are opened (matched
case-insensitively, ignoring ';1'), and only entries below it are listed.
Used for the partially fetched real discs, whose other directories are
zero-filled.

Every entry: path components, directory flag, size (lstat), SHA-256 of the
content as read through the OS (or the error), and the raw name:
  - Windows: str names from the wide-character API; 'units' is the list of
    UTF-16 code units in hex (encoded with surrogatepass, so lone
    surrogates are kept exactly).
  - POSIX: bytes names; 'raw' is the byte string in hex, and the path
    component is that byte string decoded as UTF-8 with surrogateescape.
Directory listing errors are recorded, not raised.
"""
import hashlib
import json
import os
import sys

WIN = os.name == 'nt'


def norm(n):
    return n.split(';')[0].rstrip('.').lower()


def main():
    a = sys.argv[1:]
    subtree = None
    if '--subtree' in a:
        i = a.index('--subtree')
        subtree = [c for c in a[i + 1].split('/') if c]
        del a[i:i + 2]
    top, out = a[0], a[1]
    if WIN:
        top = os.path.abspath(top)
        if not top.startswith('\\\\?\\'):
            top = '\\\\?\\' + top
    else:
        top = os.fsencode(top)
    entries, errors = [], []

    def comp(n):
        if WIN:
            u = n.encode('utf-16-le', errors='surrogatepass')
            return n, {'units': [u[i:i + 2][::-1].hex() for i in range(0, len(u), 2)]}
        return n.decode('utf-8', errors='surrogateescape'), {'raw': n.hex()}

    def walk(d, path):
        try:
            names = sorted(os.listdir(d))
        except OSError as e:
            errors.append({'path': path, 'op': 'listdir', 'error': repr(e)})
            return
        for n in names:
            p = os.path.join(d, n)
            c, raw = comp(n)
            route = False
            if subtree is not None and len(path) < len(subtree):
                if norm(c) != norm(subtree[len(path)]):
                    continue
                route = True
            e = {'path': path + [c], 'raw': raw}
            try:
                st = os.lstat(p)
            except OSError as ex:
                e['stat_error'] = repr(ex)
                entries.append(e)
                continue
            import stat
            e['dir'] = stat.S_ISDIR(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                e['symlink'] = os.fsdecode(os.readlink(p)) if not WIN else os.readlink(p)
            if e['dir']:
                if not route:
                    entries.append(e)
                walk(p, path + [c])
                continue
            if route:
                continue
            e['size'] = st.st_size
            if 'symlink' not in e:
                h = hashlib.sha256()
                n_read = 0
                try:
                    with open(p, 'rb') as fh:
                        while True:
                            b = fh.read(1 << 20)
                            if not b:
                                break
                            h.update(b)
                            n_read += len(b)
                    e['sha256'] = h.hexdigest()
                    e['bytes_read'] = n_read
                except OSError as ex:
                    e['read_error'] = repr(ex)
            entries.append(e)

    walk(top, [])
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump({'mount': os.fsdecode(top), 'entries': entries, 'errors': errors}, fh,
                  indent=1, ensure_ascii=True)
    print(f'{len(entries)} entries, {len(errors)} errors')


if __name__ == '__main__':
    main()
