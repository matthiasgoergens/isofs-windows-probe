"""Minimal ISO 9660 writer with explicit control over directory-block layout.

Writes a primary tree, optionally a Joliet tree (UCS-2 level 3) and optional
Rock Ridge (SP/ER/PX/NM) in the primary tree.  It exists so that test images
can contain layouts that ordinary mastering tools never produce on request:
empty directory blocks, multi-extent files whose continuation record starts
the next directory block, directory blocks filled to an exact byte count,
and Joliet names of up to 111 UTF-16 units (the most a directory record can
hold).

A directory is a list of items.  An item is an Entry or a layout directive:

  ('break',)          zero-pad to the end of the current block
  ('empty', n)        append n all-zero blocks (implies 'break')
  ('fill_to', used)   add zero-length filler files until exactly `used`
                      bytes of the current block are occupied
  ('fill_leave', leave[, n])  add filler files so that, after the next n
                      records (default 1), exactly `leave` bytes remain
  ('only', tree, directive)  apply directive in tree 'p' or 'j' only

Directives act per tree: filler names are generated per tree, so the primary
and Joliet trees may hold different filler files (their names say 'fill').
Entries are written in the order given; callers keep that order sorted.
"""

import hashlib
import struct

S = 2048


def both16(v):
    return struct.pack('<H', v) + struct.pack('>H', v)


def both32(v):
    return struct.pack('<I', v) + struct.pack('>I', v)


def content(tag, size):
    """Deterministic, position-dependent bytes: a reader that returns data
    from the wrong place gets a different SHA-256."""
    out = bytearray()
    i = 0
    while len(out) < size:
        out += hashlib.sha256(f'{tag}:{i}'.encode()).digest()
        i += 1
    return bytes(out[:size])


DATE7 = bytes([126, 9, 25, 0, 0, 0, 0])        # 2026-09-25 00:00:00 UTC
DATE17 = b'2026092500000000\x00'


class Entry:
    def __init__(self, iso, joliet=None, rr=None, data=None, sections=None,
                 children=None, between=None):
        """iso: primary identifier (str, e.g. 'FILE.TXT;1' or 'DIR').
        joliet: Joliet identifier (str; may contain lone surrogates via
        surrogatepass), default derived from iso.  rr: Rock Ridge NM name.
        data: file bytes (single extent).  sections: list of byte strings,
        one per extent (multi-extent file; all but the last must be a
        multiple of 2048 bytes).  children: list of items (directory).
        between: {section_index: [directives]} placed after that section's
        record."""
        self.iso = iso
        self.joliet = joliet if joliet is not None else iso
        self.rr = rr
        self.is_dir = children is not None
        self.children = children
        if sections is None and not self.is_dir:
            sections = [data if data is not None else b'']
        self.sections = sections
        self.between = between or {}
        if sections:
            for s in sections[:-1]:
                assert len(s) % S == 0, 'non-final section not block aligned'


def dir_(iso, children, joliet=None, rr=None):
    return Entry(iso, joliet, rr, children=children)


def file_(iso, data, joliet=None, rr=None):
    return Entry(iso, joliet, rr, data=data)


class Image:
    def __init__(self, root_items, joliet=True, rockridge=False,
                 volid='TEST', level3_escape='%/E'):
        self.root = Entry('\0', '\0', children=root_items)
        self.joliet = joliet
        self.rr = rockridge
        self.volid = volid
        self.escape = level3_escape.encode()
        self.fill_counter = {}

    # ---- name encoding -------------------------------------------------
    def encname(self, e, tree):
        if e.iso in ('\0', '\1'):
            return e.iso.encode()
        if tree == 'j':
            return e.joliet.encode('utf-16-be', errors='surrogatepass')
        return e.iso.encode('ascii')

    def su(self, e, tree, kind):
        """System-use bytes (Rock Ridge) for a record, primary tree only."""
        if tree != 'p' or not self.rr:
            return b''
        mode = 0o40555 if e.is_dir else 0o100444
        px = b'PX' + bytes([36, 1]) + both32(mode) + both32(1) + both32(0) + both32(0)
        out = b''
        if kind == 'root.':
            out += b'SP' + bytes([7, 1, 0xBE, 0xEF, 0])
            ident, des, src = b'RRIP_1991A', b'ROCK RIDGE', b'TEST'
            out += (b'ER' + bytes([8 + len(ident) + len(des) + len(src), 1,
                                   len(ident), len(des), len(src), 1]) + ident + des + src)
        out += px
        if kind == 'name' and e.rr is not None:
            n = e.rr.encode('utf-8')
            out += b'NM' + bytes([5 + len(n), 1, 0]) + n
        return out

    def rec(self, e, tree, extent, size, flags, name=None, kind='name'):
        n = name if name is not None else self.encname(e, tree)
        su = self.su(e, tree, kind)
        pad = 1 - len(n) % 2
        if getattr(e, 'nopad', False) and name is None:
            # 111 UTF-16 units = 222 bytes: 33 + 222 + pad byte = 256 does
            # not fit, so drop the padding byte the standard requires and
            # write an odd-length (255-byte) record.  Deliberately malformed.
            pad = 0
        ln = 33 + len(n) + pad + len(su)
        if not getattr(e, 'nopad', False):
            ln += ln % 2
        assert ln <= 255, (e.iso, ln)
        r = bytearray(ln)
        r[0] = ln
        r[2:10] = both32(extent)
        r[10:18] = both32(size)
        r[18:25] = DATE7
        r[25] = flags
        r[28:32] = both16(1)
        r[32] = len(n)
        r[33:33 + len(n)] = n
        p = 33 + len(n) + pad
        r[p:p + len(su)] = su
        return bytes(r)

    # ---- fillers -------------------------------------------------------
    def filler_lengths(self, tree):
        # map record length -> filler name length; zero-size files
        out = {}
        if tree == 'j':
            for k in range(0, 55):       # 'fillNNN' + k*'x' + '.txt;1'
                nl = 2 * (13 + k)
                out[33 + nl + 1] = k
        else:
            for k in range(0, 21):       # 'FNNN' + k*'X' + '.;1'
                nl = 7 + k
                out[33 + nl + (1 - nl % 2)] = k
        return out

    def make_fillers(self, tree, nbytes, dirkey):
        if nbytes == 0:
            return []
        lens = self.filler_lengths(tree)
        best = {0: []}
        for d in range(2, nbytes + 1, 2):
            for L in lens:
                if L <= d and (d - L) in best:
                    cand = best[d - L] + [L]
                    if d not in best or len(cand) < len(best[d]):
                        best[d] = cand
        assert nbytes in best, f'cannot fill {nbytes} bytes in tree {tree}'
        out = []
        for L in sorted(best[nbytes]):
            c = self.fill_counter.setdefault((dirkey, tree), 0)
            self.fill_counter[(dirkey, tree)] = c + 1
            k = lens[L]
            if tree == 'j':
                name = f'fill{c:03d}' + 'x' * k + '.txt;1'
            else:
                name = f'F{c:03d}' + 'X' * k + '.;1'
            f = Entry(name, name, data=b'')
            f.filler = True
            out.append(f)
        return out

    # ---- layout ----------------------------------------------------------
    def layout_dir(self, d, tree, parent):
        """Return list of (block_index, offset, entry, section_index or
        '.'/'..') plus number of blocks; also records fillers created."""
        recs = []   # (kind, entry, section)
        recs.append(('rec', d, '.'))
        recs.append(('rec', parent, '..'))
        for it in d.children:
            if isinstance(it, Entry):
                if it.is_dir:
                    recs.append(('rec', it, 0))
                else:
                    for i in range(len(it.sections)):
                        recs.append(('rec', it, i))
                        for dv in it.between.get(i, []):
                            recs.append(('dir', dv, None))
            else:
                recs.append(('dir', it, None))
        placed = []
        blk, pos = 0, 0
        fill_created = []

        def reclen(e, sec):
            if sec in ('.', '..'):
                return len(self.rec(e, tree, 0, 0, 2, name=b'\0',
                                    kind='root.' if (sec == '.' and d is self.root) else 'dot'))
            return len(self.rec(e, tree, 0, 0, 0))

        def put(e, sec):
            nonlocal blk, pos
            L = reclen(e, sec)
            if pos + L > S:
                blk, pos = blk + 1, 0
            placed.append((blk, pos, e, sec))
            pos += L

        i = 0
        while i < len(recs):
            kind, a, sec = recs[i]
            if kind == 'rec':
                put(a, sec)
            else:
                if a[0] == 'only':
                    if a[1] != tree:
                        i += 1
                        continue
                    a = a[2]
                op = a[0]
                if op == 'break':
                    if pos:
                        blk, pos = blk + 1, 0
                elif op == 'empty':
                    if pos:
                        blk, pos = blk + 1, 0
                    blk += a[1]
                elif op in ('fill_to', 'fill_leave'):
                    if op == 'fill_to':
                        need = a[1] - pos
                    else:
                        nrec = a[2] if len(a) > 2 else 1
                        nxt = recs[i + 1:i + 1 + nrec]
                        assert all(x[0] == 'rec' for x in nxt)
                        need = S - a[1] - sum(reclen(x[1], x[2]) for x in nxt) - pos
                    assert need >= 0, (op, a, pos)
                    for f in self.make_fillers(tree, need, id(d)):
                        put(f, 0)
                        fill_created.append(f)
                    assert (op != 'fill_to') or pos == a[1], (pos, a)
                else:
                    raise ValueError(op)
            i += 1
        nblocks = blk + (1 if pos else 0)
        return placed, nblocks, fill_created

    # ---- build ---------------------------------------------------------
    def build(self):
        trees = ['p'] + (['j'] if self.joliet else [])
        # Pass 1: layouts (sizes only), per tree.
        lay = {}
        order = {}
        for t in trees:
            lay[t] = {}
            order[t] = []
            q = [(self.root, self.root)]
            while q:          # breadth first: path table order
                nq = []
                for d, par in q:
                    placed, nb, fills = self.layout_dir(d, t, par)
                    lay[t][id(d)] = (d, par, placed, nb)
                    order[t].append(d)
                    for it in d.children:
                        if isinstance(it, Entry) and it.is_dir:
                            nq.append((it, d))
                q = nq
        # Allocation: 16 PVD, 17 SVD?, then terminator, path tables,
        # directories, then file data.
        lba = 16
        pvd_lba = lba; lba += 1
        svd_lba = None
        if self.joliet:
            svd_lba = lba; lba += 1
        term_lba = lba; lba += 1
        pt = {}
        for t in trees:
            ptb = self.path_table_bytes(t, order[t], {}, little=True)
            nb = (len(ptb) + S - 1) // S
            pt[t] = {'L': lba, 'M': lba + nb, 'size': len(ptb), 'nb': nb}
            lba += 2 * nb
        dir_lba = {}
        for t in trees:
            for d in order[t]:
                nb = lay[t][id(d)][3]
                dir_lba[(t, id(d))] = lba
                lba += nb
        # File data: shared between trees; each section separately with a
        # one-block gap of marker bytes so that a reader treating sections
        # as contiguous reads wrong data.
        data_lba = {}
        blobs = []
        seen = set()

        def alloc_files(d):
            nonlocal lba
            for it in d.children:
                if not isinstance(it, Entry):
                    continue
                if it.is_dir:
                    alloc_files(it)
                    continue
                if id(it) in seen:
                    continue
                seen.add(id(it))
                locs = []
                for si, sdata in enumerate(it.sections):
                    if len(sdata) == 0:
                        locs.append(0)
                        continue
                    if len(it.sections) > 1:
                        blobs.append((lba, content(f'gap:{it.iso}:{si}', S)))
                        lba += 1
                    locs.append(lba)
                    blobs.append((lba, sdata))
                    lba += (len(sdata) + S - 1) // S
                data_lba[id(it)] = locs
        alloc_files(self.root)
        lba += 150      # trailing zero padding, as mkisofs -pad / xorriso write
        total = lba
        img = bytearray(total * S)
        for at, b in blobs:
            img[at * S:at * S + len(b)] = b
        # Directories.
        for t in trees:
            for d in order[t]:
                d_, par, placed, nb = lay[t][id(d)]
                base = dir_lba[(t, id(d))] * S
                for blk, off, e, sec in placed:
                    if sec == '.':
                        r = self.rec(d, t, dir_lba[(t, id(d))], nb * S, 2, name=b'\0',
                                     kind='root.' if d is self.root else 'dot')
                    elif sec == '..':
                        pnb = lay[t][id(par)][3]
                        r = self.rec(par, t, dir_lba[(t, id(par))], pnb * S, 2, name=b'\1',
                                     kind='dot')
                    elif e.is_dir:
                        enb = lay[t][id(e)][3]
                        r = self.rec(e, t, dir_lba[(t, id(e))], enb * S, 2)
                    else:
                        if getattr(e, 'filler', False):
                            ext, size, fl = 0, 0, 0
                        else:
                            ext = data_lba[id(e)][sec]
                            size = len(e.sections[sec])
                            fl = 0x80 if sec < len(e.sections) - 1 else 0
                        r = self.rec(e, t, ext, size, fl)
                    p = base + blk * S + off
                    assert off + len(r) <= S
                    img[p:p + len(r)] = r
        # Path tables.
        for t in trees:
            for little, key in ((True, 'L'), (False, 'M')):
                ptb = self.path_table_bytes(t, order[t], dir_lba, little)
                p = pt[t][key] * S
                img[p:p + len(ptb)] = ptb
        # Volume descriptors.
        root_rec = {}
        for t in trees:
            nb = lay[t][id(self.root)][3]
            r = bytearray(34)
            r[0] = 34
            r[2:10] = both32(dir_lba[(t, id(self.root))])
            r[10:18] = both32(nb * S)
            r[18:25] = DATE7
            r[25] = 2
            r[28:32] = both16(1)
            r[32] = 1
            root_rec[t] = bytes(r)
        img[pvd_lba * S:pvd_lba * S + S] = self.vd(1, total, pt['p'], root_rec['p'])
        if self.joliet:
            img[svd_lba * S:svd_lba * S + S] = self.vd(2, total, pt['j'], root_rec['j'])
        tvd = bytearray(S)
        tvd[0] = 255
        tvd[1:7] = b'CD001\x01'
        img[term_lba * S:term_lba * S + S] = tvd
        return bytes(img)

    def path_table_bytes(self, t, order, dir_lba, little):
        num = {id(d): i + 1 for i, d in enumerate(order)}
        parent = {id(self.root): self.root}
        for d in order:
            for it in d.children:
                if isinstance(it, Entry) and it.is_dir:
                    parent[id(it)] = d
        out = bytearray()
        for d in order:
            n = b'\0' if d is self.root else self.encname(d, t)
            if t == 'j' and getattr(d, 'pt_joliet', None) is not None:
                # Deliberately inconsistent: path table name differs from
                # the directory record's name.
                n = d.pt_joliet.encode('utf-16-be')
            ext = dir_lba.get((t, id(d)), 0)
            pn = num[id(parent[id(d)])]
            if little:
                out += bytes([len(n), 0]) + struct.pack('<IH', ext, pn)
            else:
                out += bytes([len(n), 0]) + struct.pack('>IH', ext, pn)
            out += n
            if len(n) % 2:
                out += b'\0'
        return bytes(out)

    def vd(self, typ, total, pt, root):
        v = bytearray(S)
        v[0] = typ
        v[1:7] = b'CD001\x01'
        j = typ == 2

        def s(txt, n):
            if j:
                b = txt.encode('utf-16-be')
                return (b + ' '.encode('utf-16-be') * n)[:n]
            return (txt.encode() + b' ' * n)[:n]
        v[8:40] = s('', 32)
        v[40:72] = s(self.volid, 32)
        v[80:88] = both32(total)
        if j:
            v[88:91] = self.escape
        v[120:124] = both16(1)
        v[124:128] = both16(1)
        v[128:132] = both16(S)
        v[132:140] = both32(pt['size'])
        v[140:144] = struct.pack('<I', pt['L'])
        v[148:152] = struct.pack('>I', pt['M'])
        v[156:190] = root
        v[190:318] = s('', 128)
        v[318:446] = s('', 128)
        v[446:574] = s('', 128)
        v[574:702] = s('ISOFS LAYOUT TEST HARNESS', 128)
        v[702:739] = s('', 37)
        v[739:776] = s('', 37)
        v[776:813] = s('', 37)
        v[813:830] = DATE17
        v[830:847] = DATE17
        v[847:864] = b'0' * 16 + b'\0'
        v[864:881] = b'0' * 16 + b'\0'
        v[881] = 1
        return bytes(v)
