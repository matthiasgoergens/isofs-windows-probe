#!/usr/bin/env python3
"""Fetch only the parts of a remote ISO image needed to list one directory.

  partial.py fetch URL SIZE /DIR/PATH OUT.partial [--content-cap BYTES]
  partial.py expand IN.partial OUT.iso

'fetch' reads, with HTTP Range requests, the volume descriptors, the path
tables and root directories of the primary and Joliet trees, every
directory on the way to /DIR/PATH (matched case-insensitively, ignoring
';1') and that directory itself, and the content of the files in it if they
total at most --content-cap bytes (default 64 MiB).  The result is a list
of (offset, bytes) chunks.  'expand' writes a full-size image that holds
those chunks and zeros everywhere else.  An OS can mount such an image and
list the target directory exactly as on the real disc, because every byte
that listing reads is the disc's own.  Other directories read as zeros.
"""
import json
import struct
import sys
import time
import urllib.request

S = 2048
MAGIC = b'ISOPART1'


class Remote:
    def __init__(self, url):
        self.url = url
        self.chunks = {}

    def get(self, off, n):
        for attempt in range(6):
            try:
                req = urllib.request.Request(self.url, headers={
                    'Range': f'bytes={off}-{off + n - 1}',
                    'User-Agent': 'isofs-layout-test-harness'})
                with urllib.request.urlopen(req, timeout=120) as r:
                    b = r.read()
                if len(b) != n:
                    raise IOError(f'short read {len(b)} != {n}')
                self.chunks[off] = b
                return b
            except Exception as e:
                print(f'retry {attempt}: {e}', file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        raise IOError(f'failed to fetch {off}+{n}')


def norm(n):
    return n.split(';')[0].rstrip('.').lower()


def fetch(url, size, target, out, cap):
    r = Remote(url)
    head = r.get(16 * S, 8 * S)
    trees = []
    for i in range(8):
        vd = head[i * S:(i + 1) * S]
        if vd[1:6] != b'CD001' or vd[0] == 255:
            break
        if vd[0] == 1:
            trees.append(('primary', vd))
        if vd[0] == 2 and vd[88:91] in (b'%/@', b'%/C', b'%/E'):
            trees.append(('joliet', vd))
    want = [c for c in target.split('/') if c]
    content = []
    for tname, vd in trees:
        ptsize = struct.unpack_from('<I', vd, 132)[0]
        nb = (ptsize + S - 1) // S
        r.get(struct.unpack_from('<I', vd, 140)[0] * S, nb * S)
        r.get(struct.unpack_from('>I', vd, 148)[0] * S, nb * S)
        ext, ln = struct.unpack_from('<I', vd, 158)[0], struct.unpack_from('<I', vd, 166)[0]
        depth = 0
        while True:
            d = r.get(ext * S, (ln + S - 1) // S * S)
            if depth == len(want):
                break
            nxt = None
            for b in range(0, len(d), S):
                o = 0
                while o < S and d[b + o]:
                    rec = d[b + o:b + o + d[b + o]]
                    nl = rec[32]
                    raw = rec[33:33 + nl]
                    name = (raw.decode('utf-16-be', errors='replace') if tname == 'joliet'
                            else raw.decode('latin-1'))
                    if rec[25] & 2 and norm(name) == norm(want[depth]):
                        nxt = (struct.unpack_from('<I', rec, 2)[0],
                               struct.unpack_from('<I', rec, 10)[0])
                    o += d[b + o]
            if nxt is None:
                raise SystemExit(f'{tname}: {want[depth]} not found')
            ext, ln = nxt
            depth += 1
        for b in range(0, len(d), S):
            o = 0
            while o < S and d[b + o]:
                rec = d[b + o:b + o + d[b + o]]
                if not rec[25] & 2:
                    content.append((struct.unpack_from('<I', rec, 2)[0],
                                    struct.unpack_from('<I', rec, 10)[0]))
                o += d[b + o]
    content = sorted(set(content))
    total = sum(n for _, n in content)
    fetched_content = total <= cap
    if fetched_content:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda en: r.get(en[0] * S, en[1]), [c for c in content if c[1]]))
    hdr = {'url': url, 'size': size, 'target': target, 'content_fetched': fetched_content,
           'content_bytes': total,
           'chunks': [[o, len(b)] for o, b in sorted(r.chunks.items())]}
    with open(out, 'wb') as f:
        h = json.dumps(hdr).encode()
        f.write(MAGIC + struct.pack('<I', len(h)) + h)
        for o, b in sorted(r.chunks.items()):
            f.write(b)
    print(f'{out}: {len(r.chunks)} chunks, {sum(len(b) for b in r.chunks.values())} bytes, '
          f'content {total} bytes fetched={fetched_content}')


def expand(inp, out):
    with open(inp, 'rb') as f:
        assert f.read(8) == MAGIC
        hl = struct.unpack('<I', f.read(4))[0]
        hdr = json.loads(f.read(hl))
        with open(out, 'wb') as g:
            g.truncate(hdr['size'])
            for o, n in hdr['chunks']:
                g.seek(o)
                g.write(f.read(n))
    print(json.dumps({k: v for k, v in hdr.items() if k != 'chunks'}))


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[0] == 'fetch':
        cap = 64 << 20
        if '--content-cap' in a:
            i = a.index('--content-cap')
            cap = int(a[i + 1])
            del a[i:i + 2]
        fetch(a[1], int(a[2]), a[3], a[4], cap)
    elif a[0] == 'expand':
        expand(a[1], a[2])
