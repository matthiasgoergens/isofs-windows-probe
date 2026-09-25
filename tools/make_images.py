#!/usr/bin/env python3
"""Build every crafted test image into OUTDIR, plus oracle JSON for each.

  make_images.py OUTDIR

Crafted images are written by isowriter.py.  Two images come from xorriso
(an ordinary positive control and an ordinary Rock Ridge + Joliet image);
they are skipped with a warning if xorriso is not installed.  Images found
in ../images/ (committed ones such as long-patched.iso, and any added
later, e.g. images made by PowerISO) are copied in as they are.

Most crafted cases come in two flavours: '-j' has primary + Joliet trees
(Windows and macOS read Joliet), '-p' has the primary tree only.
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isowriter import Image, Entry, dir_, file_, content, S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
built = []


def write(name, img):
    p = os.path.join(OUT, name + '.iso')
    with open(p, 'wb') as f:
        f.write(img.build())
    built.append(p)


def both(name, items_fn, **kw):
    """Build name-j (Joliet + primary) and name-p (primary only)."""
    write(name + '-j', Image(items_fn(), joliet=True, **kw))
    write(name + '-p', Image(items_fn(), joliet=False, **kw))


def f(iso, size, joliet=None, rr=None):
    return file_(iso, content(iso, size), joliet, rr)


# ---------------------------------------------------------------- Q1 long
def long_items(odd):
    """odd=False: names up to 110 UTF-16 units, the most a record with the
    mandatory padding byte can hold (33 + 220 + 1 = 254).  odd=True: 111
    units in 255-byte records without the padding byte (malformed)."""
    K = '\u6f22'      # 3 UTF-8 bytes
    Z = '\u5b57'
    items = []
    # (primary name, joliet name).  Units include ';1' where present.
    if not odd:
        for n in (64, 85, 86, 100, 103, 110):
            items.append((f'K{n:03d}.TXT;1', 'K' + K * (n - 3) + ';1'))
        items.append(('N086.TXT;1', 'N' + K * 85))                      # no ';1'
        items.append(('N110.TXT;1', 'N' + K * 109))
        items.append(('A110.TXT;1', 'a' * 108 + ';1'))
        items.append(('E110.TXT;1', 'e' + '\U0001F600' * 53 + 'e;1'))  # 53 pairs
        items.append(('G086.TXT;1', 'g' + '\u0a97' * 83 + ';1'))       # Gujarati
        inner, dname = 'i' + Z * 107 + ';1', 'D' + Z * 109              # 110, 110
    else:
        items.append(('K111.TXT;1', 'K' + K * 108 + ';1'))
        items.append(('N111.TXT;1', 'N' + K * 110))
        items.append(('A111.TXT;1', 'a' * 109 + ';1'))
        inner, dname = 'i' + Z * 108 + ';1', 'D' + Z * 110
    out = [f(p, 100 + i * 7, j) for i, (p, j) in enumerate(items)]
    d = dir_('DLONG', [f('INNER.TXT;1', 333, inner)], joliet=dname)
    out.append(d)
    for e in out + d.children:
        e.nopad = odd
    return sorted(out, key=lambda e: e.iso)


write('long-j', Image(long_items(False), joliet=True))
write('long111-j', Image(long_items(True), joliet=True))


# ------------------------------------------------- Q2 empty directory blocks
def base_files(prefix):
    return [f(f'{prefix}1.TXT;1', 3000, f'{prefix}1-real.txt;1'),
            f(f'{prefix}2.TXT;1', 5000, f'{prefix}2-real.txt;1')]


def pattern_dir(dname, used_p, used_j, trail_p, trail_j, extra_after=()):
    """Directory whose blocks are filled to exactly used_p[i] / used_j[i]
    bytes, followed by trail_* empty blocks, plus a sibling afterwards."""
    ch = base_files('A')
    for tree, used, trail in (('p', used_p, trail_p), ('j', used_j, trail_j)):
        for i, u in enumerate(used):
            ch.append(('only', tree, ('fill_to', u)))
            if i < len(used) - 1:
                ch.append(('only', tree, ('break',)))
        if trail:
            ch.append(('only', tree, ('empty', trail)))
    return ch


def xtras():      # Easy CD Creator 4.2, DM_BXL2 /XTRAS: 2022 1526 E
    return [dir_('XTRAS', pattern_dir('XTRAS', [2022, 1526], [2022, 1526], 1, 1)),
            f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]


def images():     # Nero, Comdex_05 /IMAGES: 2012 2048 E
    return [dir_('IMAGES', pattern_dir('IMAGES', [2012, 2048], [2012, 2048], 1, 1)),
            f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]


def disk13():     # CDEverywhere, ITSOFTCD 39 DISK13, both trees as on the disc
    p = [2024] + [1950] * 8 + [1946, 2028, 2046, 2026, 2024, 1520]
    j = [1990, 2046, 2046, 2046, 2048, 1994, 1560]
    return [dir_('DISK13', pattern_dir('DISK13', p, j, 1, 2)),
            f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]


def nero_min():   # block 0 exactly full, one trailing empty block
    return [dir_('NERO', base_files('A') + [('fill_to', 2048), ('empty', 1)]),
            f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]


def mid_empty():  # malformed: empty block in the middle, records after it
    return [dir_('MID', [f('A1.TXT;1', 3000, 'a1-before.txt;1'),
                         ('empty', 1),
                         f('B1.TXT;1', 4000, 'b1-after-empty.txt;1'),
                         f('B2.TXT;1', 5000, 'b2-after-empty.txt;1')]),
            f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]


def mid_empty_full():  # as mid_empty but block 0 is full to 2048 first
    return [dir_('MIDF', [f('A1.TXT;1', 3000, 'a1-before.txt;1'), ('fill_to', 2048),
                          ('empty', 1),
                          f('B1.TXT;1', 4000, 'b1-after-empty.txt;1')]),
            f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]


def mext_sections(tag):
    return [content(tag + '0', 2 * S), content(tag + '1', S), content(tag + '2', 1000)]


def mext_empty():  # malformed: multi-extent record, then an empty block
    big = Entry('BIG.BIN;1', 'big.bin;1', sections=mext_sections('mextempty'),
                between={0: [('empty', 1)]})
    return [dir_('MEXTE', [f('A1.TXT;1', 3000, 'a1.txt;1'), ('fill_leave', 60), big,
                           f('C1.TXT;1', 2000, 'c1-after.txt;1')]),
            f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]


both('empty-xtras', xtras)
both('empty-images', images)
both('empty-disk13', disk13)
both('empty-nero', nero_min)
both('empty-mid', mid_empty)
both('empty-midfull', mid_empty_full)
both('empty-mext', mext_empty)


# ------------------------------------------------------ Q3 multi-extent
def mext(kind):
    def items():
        between = {}
        pre = []
        if kind == 'tail':     # section 0 ends 20 bytes before block end
            pre = [('fill_leave', 20)]
        elif kind == 'full':   # section 0 record ends exactly at block end
            pre = [('fill_leave', 0)]
        elif kind == 'break':  # section 0 early in block, rest zero padded
            between = {0: [('break',)]}
        elif kind == 'second':  # sections 0 and 1 end the block; 2 crosses
            pre = [('fill_leave', 0, 2)]
        big = Entry('BIG.BIN;1', 'big.bin;1', sections=mext_sections('mext' + kind),
                    between=between)
        return [dir_('MEXT', [f('A1.TXT;1', 3000, 'a1.txt;1')] + pre + [big,
                              f('C1.TXT;1', 2000, 'c1-after.txt;1')]),
                f('ZAFTER.TXT;1', 1234, 'zafter.txt;1')]
    return items


for k in ('same', 'tail', 'full', 'break', 'second'):
    both('mext-' + k, mext(k))


# ---------------------------------------------------- Q4 which tree is used
def treeid(rr):
    def items():
        return [dir_('PDIR', [f('PINNER.TXT;1', 700, 'joliet-inner.txt;1',
                                'rockridge-inner.txt' if rr else None)],
                     joliet='joliet-dir', rr='rockridge-dir' if rr else None),
                f('PRIMARY.TXT;1', 500, 'joliet-tree.txt;1',
                  'rockridge-tree.txt' if rr else None)]
    return items


write('treeid-rr-j', Image(treeid(True)(), joliet=True, rockridge=True))
write('treeid-rr-p', Image(treeid(True)(), joliet=False, rockridge=True))
write('treeid-j', Image(treeid(False)(), joliet=True))


# ------------------------------------------ path table vs directory record
def ptmismatch():
    a = dir_('ADIR', [f('INNER.TXT;1', 700, 'inner-a.txt;1')], joliet='adir-record-name')
    a.pt_joliet = 'adir-PATHTABLE-name'   # same length class, different name
    b = dir_('BDIR', [f('INNER.TXT;1', 800, 'inner-b.txt;1')], joliet='bdir-consistent')
    return [a, b]


write('ptmismatch-j', Image(ptmismatch(), joliet=True))


# ------------------------------------------------------- xorriso images
def xorriso_images():
    if not shutil.which('xorriso'):
        print('WARNING: xorriso missing, skipping xorriso images', file=sys.stderr)
        return
    src = os.path.join(OUT, '_src')
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(os.path.join(src, 'ctl', 'sub dir', 'deeper'))
    files = {
        'ctl/readme.txt': content('ctl1', 1500),
        'ctl/Mixed Case Name.TXT': content('ctl2', 4096),
        'ctl/café-漢字.dat': content('ctl3', 70000),
        'ctl/sub dir/inner.bin': content('ctl4', 2049),
        'ctl/sub dir/deeper/empty.txt': b'',
    }
    for p, b in files.items():
        with open(os.path.join(src, p), 'wb') as fh:
            fh.write(b)
    base = ['xorriso', '-no_rc', '-report_about', 'SORRY']
    subprocess.run(base + ['-outdev', os.path.join(OUT, 'control-xorriso.iso'),
                           '-joliet', 'on', '-map', os.path.join(src, 'ctl'), '/',
                           '-commit'], check=True)
    # Rock Ridge + Joliet: a 100-character name (Joliet shortens it to 64
    # units without joliet_long_names), a symlink, and a name that differs
    # only in case from its ISO 9660 form.
    os.makedirs(os.path.join(src, 'rrj', 'Dir'))
    long = 'rr-' + 'L' * 93 + '.txt'
    with open(os.path.join(src, 'rrj', long), 'wb') as fh:
        fh.write(content('rrj1', 800))
    with open(os.path.join(src, 'rrj', 'lower.txt'), 'wb') as fh:
        fh.write(content('rrj2', 900))
    with open(os.path.join(src, 'rrj', 'Dir', 'x.txt'), 'wb') as fh:
        fh.write(content('rrj3', 10))
    os.symlink('lower.txt', os.path.join(src, 'rrj', 'link-to-lower'))
    subprocess.run(base + ['-outdev', os.path.join(OUT, 'rrjoliet-xorriso.iso'),
                           '-rockridge', 'on', '-joliet', 'on', '-map',
                           os.path.join(src, 'rrj'), '/', '-commit'], check=True)
    built.append(os.path.join(OUT, 'control-xorriso.iso'))
    built.append(os.path.join(OUT, 'rrjoliet-xorriso.iso'))
    shutil.rmtree(src)


xorriso_images()

# ------------------------------------------------------ committed images
extra = os.path.join(HERE, '..', 'images')
if os.path.isdir(extra):
    for n in sorted(os.listdir(extra)):
        if n.lower().endswith('.iso'):
            shutil.copy(os.path.join(extra, n), os.path.join(OUT, n))
            built.append(os.path.join(OUT, n))

# ------------------------------------------------------ oracles
for p in built:
    with open(p[:-4] + '.oracle.json', 'w') as fh:
        subprocess.run([sys.executable, os.path.join(HERE, 'oracle.py'), p], stdout=fh,
                       check=True)
tot = sum(os.path.getsize(p) for p in built)
print(f'{len(built)} images, {tot} bytes')
