#!/usr/bin/env python3
"""Independent reader: list what an ISO 9660 image contains, per tree.

  oracle.py IMAGE [--subtree /A/B] [--no-hash] > oracle.json

For each tree present (primary, joliet, rockridge) it walks the directory
hierarchy from the root record and emits every entry with its name as
recorded, directory flag, size and SHA-256 of the content.

Walk semantics are deliberately lenient, so that the oracle describes what
is *on the medium*: a zero length byte moves to the next block (at any
offset), stopping at the directory's data length; consecutive records with
the same name where the earlier one has the multi-extent flag (0x80) are
merged into one file whose content is the concatenation of the sections.
Anomalies (empty blocks followed by records, multi-extent chains that cross
an empty block or a block boundary) are reported per directory so that the
comparison can say which layout each OS result refers to.

Names: 'joliet' = UTF-16BE decoded with surrogatepass (lone surrogates are
kept); 'primary' = bytes decoded as latin-1; 'rockridge' = NM (UTF-8,
surrogateescape), falling back to the primary name.  Names are raw: no ';1'
stripping.  The comparison applies normalisations explicitly.
"""
import hashlib
import json
import struct
import sys

S = 2048


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


class Img:
    def __init__(self, path):
        self.f = open(path, 'rb')
        self.f.seek(0, 2)
        self.size = self.f.tell()

    def read(self, off, n):
        self.f.seek(off)
        return self.f.read(n)


def vds(img):
    out = {}
    for i in range(16, 100):
        vd = img.read(i * S, S)
        if len(vd) < S or vd[1:6] != b'CD001':
            break
        if vd[0] == 255:
            break
        if vd[0] == 1 and 'primary' not in out:
            out['primary'] = vd
        if vd[0] == 2 and vd[88:91] in (b'%/@', b'%/C', b'%/E') and 'joliet' not in out:
            out['joliet'] = vd
    return out


def rr_parse(su):
    """Return (NM name bytes or None, set of signatures) from a SUSP area
    (continuation areas are not followed; the crafted images do not use
    them and are small enough for NM to fit)."""
    name, sigs, p = None, set(), 0
    while p + 4 <= len(su):
        sig, ln = su[p:p + 2], su[p + 2]
        if ln < 4 or p + ln > len(su):
            break
        sigs.add(sig.decode('latin-1'))
        if sig == b'NM':
            flags = su[p + 4]
            if not flags & 6:
                name = (name or b'') + su[p + 5:p + ln]
        p += ln
    return name, sigs


def walk(img, tree, root, subtree=None, do_hash=True):
    entries, anomalies = [], []
    rr = tree == 'rockridge'

    def records(ext, dlen, dpath):
        """Yield (block, offset, record bytes)."""
        nblk = (dlen + S - 1) // S
        data = img.read(ext * S, nblk * S)
        empties, saw_empty_then_record = [], False
        for b in range(nblk):
            blk = data[b * S:(b + 1) * S]
            if len(blk) < S or blk[0] == 0:
                empties.append(b)
                continue
            if empties and empties[-1] < b:
                saw_empty_then_record = True
            off = 0
            while off < S and off < dlen - b * S:
                ln = blk[off]
                if ln == 0:
                    break
                yield b, off, blk[off:off + ln]
                off += ln
        if empties:
            anomalies.append({'dir': dpath, 'blocks': nblk, 'empty_blocks': empties,
                              'empty_then_records': saw_empty_then_record})

    def name_of(r):
        nl = r[32]
        raw = r[33:33 + nl]
        if tree == 'joliet':
            return raw.decode('utf-16-be', errors='surrogatepass')
        if rr:
            p = 33 + nl + (1 - nl % 2)
            nm, _ = rr_parse(r[p:])
            if nm is not None:
                return nm.decode('utf-8', errors='surrogateescape')
        return raw.decode('latin-1')

    def content_hash(sections):
        if not do_hash:
            return None
        h = hashlib.sha256()
        for ext, ln in sections:
            if ext * S + ln > img.size:
                return None
            h.update(img.read(ext * S, ln))
        return h.hexdigest()

    seen = set()

    def rec_dir(ext, dlen, path, emit):
        if ext in seen:
            return
        seen.add(ext)
        pending = None      # multi-extent chain being assembled
        for b, off, r in records(ext, dlen, '/' + '/'.join(path)):
            nl = r[32]
            if nl == 1 and r[33] in (0, 1):
                continue
            name = name_of(r)
            flags = r[25]
            is_link = False
            if rr:
                _p = 33 + nl + (1 - nl % 2)
                is_link = 'SL' in rr_parse(r[_p:])[1]
            e_ext, e_len = u32(r, 2), u32(r, 10)
            if pending is not None and pending['name'] == name:
                pending['sections'].append((e_ext, e_len))
                pending['where'].append((b, off))
            else:
                if pending is not None:
                    finish(pending, emit)
                pending = {'name': name, 'dir': bool(flags & 2), 'sections': [(e_ext, e_len)],
                           'where': [(b, off)], 'path': path + [name], 'symlink': is_link}
            if not flags & 0x80:
                finish(pending, emit)
                pending = None
        if pending is not None:
            pending['unterminated'] = True
            finish(pending, emit)

    def norm(n):
        return n.split(';')[0].rstrip('.').lower()

    def matches(path):
        """'in' if path is inside the subtree, 'route' if on the way."""
        if subtree is None:
            return 'in'
        k = min(len(path), len(subtree))
        if [norm(c) for c in path[:k]] != [norm(c) for c in subtree[:k]]:
            return None
        return 'in' if len(path) > len(subtree) else 'route'

    def finish(p, emit):
        m = matches(p['path'])
        if m is None:
            return
        e = {'path': p['path'], 'dir': p['dir']}
        if p['dir']:
            ext, ln = p['sections'][0]
            if m == 'in':
                entries.append(e)
            rec_dir(ext, ln, p['path'], True)
            return
        if m != 'in':
            return
        if p.get('symlink'):
            e['symlink'] = True
        e['size'] = sum(ln for _, ln in p['sections'])
        e['sections'] = len(p['sections'])
        if len(p['sections']) > 1:
            blocks = [w[0] for w in p['where']]
            e['mext_blocks'] = blocks
            e['mext_crosses_block'] = len(set(blocks)) > 1
        if p.get('unterminated'):
            e['mext_unterminated'] = True
        e['sha256'] = content_hash(p['sections'])
        entries.append(e)

    rext, rlen = u32(root, 2), u32(root, 10)
    rec_dir(rext, rlen, [], True)
    return entries, anomalies


def main():
    args = sys.argv[1:]
    subtree, do_hash = None, True
    if '--subtree' in args:
        i = args.index('--subtree')
        subtree = [c for c in args[i + 1].split('/') if c]
        del args[i:i + 2]
    if '--no-hash' in args:
        args.remove('--no-hash')
        do_hash = False
    img = Img(args[0])
    v = vds(img)
    out = {'image': args[0].replace('\\', '/').split('/')[-1], 'trees': {}}
    if 'primary' in v:
        root = v['primary'][156:190]
        # Rock Ridge present if root '.' has SP
        d = img.read(u32(root, 2) * S, S)
        nl = d[32]
        su = d[33 + nl + (1 - nl % 2):d[0]]
        _, sigs = rr_parse(su)
        trees = ['primary'] + (['rockridge'] if 'SP' in sigs else [])
        for t in trees:
            ents, an = walk(img, t, root, subtree, do_hash)
            out['trees'][t] = {'entries': ents, 'anomalies': an}
    if 'joliet' in v:
        ents, an = walk(img, 'joliet', v['joliet'][156:190], subtree, do_hash)
        out['trees']['joliet'] = {'entries': ents, 'anomalies': an}
    json.dump(out, sys.stdout, indent=1, ensure_ascii=True)
    print()


if __name__ == '__main__':
    main()
