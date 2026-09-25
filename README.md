# isofs-windows-probe

A throwaway test harness. It checks how the ISO 9660 / Joliet drivers of
Windows, macOS and the BSDs read a few unusual but real-world directory
layouts. It runs only on GitHub-hosted runners (the BSDs in VMs), and it
mounts the test images read-only.

What is tested:

* **Long Joliet names.** Names of 64 to 110 UTF-16 units: CJK, Gujarati,
  emoji (surrogate pairs) and ASCII. The spec allows 64, and PowerISO and
  UltraISO write up to 110. `long111-j` holds 111-unit names in 255-byte
  records without the mandatory padding byte, so it is deliberately
  malformed.
* **Empty directory blocks.** Directories whose data length includes an
  all-zero 2048-byte block. There are crafted copies of three real-disc
  layouts (bytes used per block: `2022 1526 E`, `2012 2048 E`, and a
  16-block directory ending in `E`). There are also malformed variants: an
  empty block between records, and an empty block right after a record
  that carries the multi-extent flag.
* **Multi-extent files** (ISO level 3) whose continuation record starts the
  next directory block. This layout is legal.
* **Tree selection.** Each tree (primary, Joliet, Rock Ridge) of an image
  carries different names, which shows which tree the OS reads.
* A positive control built with xorriso. `compare.py --selftest` shows that
  the comparison detects a renamed file, a wrong hash and a wrong size.

Layout:

* `tools/isowriter.py`: a minimal ISO 9660 writer with explicit control
  over directory-block layout.
* `tools/make_images.py`: builds the crafted images and an oracle for each.
* `tools/oracle.py`: an independent reader. It lists every entry of each
  tree with its raw name, size and SHA-256.
* `tools/lister.py`: lists a mounted file system as the OS presents it,
  with raw UTF-16 units on Windows and raw bytes elsewhere.
* `tools/compare.py`: compares the listing with the oracle, trying name
  transforms in order: version strip, case, NFC, `?` substitution.
* `ci/run-windows.ps1`, `ci/run-posix.sh`: mount, list, compare and
  unmount, for each image and each mount-option variant.
* `tools/partial.py`, `ci/fetch-real.sh`: fetch with HTTP Range requests
  only the sectors needed to list one directory of three archive.org discs.
  The job is optional. The discs are not stored here.
* `images/`: committed test images. `long-patched.iso` has Joliet names of
  up to 103 units, patched in place. Images made by other tools can be
  dropped here, and each one is tested with an oracle computed from the
  image itself.
