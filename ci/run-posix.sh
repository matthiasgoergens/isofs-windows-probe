#!/bin/sh
# Mount every image in IMGDIR read-only with the native ISO 9660 driver,
# list it with tools/lister.py, compare with the oracle, unmount.
#
#   run-posix.sh IMGDIR OUTDIR [SUBTREES.json]
#
# macOS: hdiutil attach (the system chooses the driver), then mount_cd9660
# directly with mount-option variants.  FreeBSD: mdconfig + mount_cd9660
# with option variants.  NetBSD/OpenBSD: vnconfig/vndconfig + mount_cd9660.
# Runs as root on the BSD VMs; uses sudo on macOS.
set -u
IMGDIR=$1
OUT=$2
SUBS=${3:-}
HERE=$(cd "$(dirname "$0")" && pwd)
TOOLS=$HERE/../tools
OS=$(uname -s)
PY=$(command -v python3 || command -v python3.13 || command -v python3.12 || command -v python3.11 || true)
SUDO=""
[ "$(id -u)" = 0 ] || SUDO=sudo
MNT=/tmp/isomnt
mkdir -p "$OUT"
$SUDO mkdir -p $MNT

{
    echo "uname: $(uname -a)"
    case $OS in
    Darwin) sw_vers; echo "cd9660.kext: $(ls -d /System/Library/Extensions/cd9660.kext 2>&1)";
            echo "cd9660.fs: $(ls /System/Library/Filesystems | grep -i -e cd9660 -e udf | tr '\n' ' ')";;
    FreeBSD) freebsd-version -kru;;
    NetBSD|OpenBSD) sysctl kern.version;;
    esac
    echo "python: $($PY --version 2>&1)"
} > "$OUT/sysinfo.txt" 2>&1
cat "$OUT/sysinfo.txt"

subtree_for() {   # image basename -> subtree or empty
    [ -n "$SUBS" ] && [ -f "$SUBS" ] || return 0
    $PY -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ''))" "$SUBS" "$1"
}

# list_and_compare RESULTDIR MOUNTPOINT ISO
list_and_compare() {
    d=$1; mp=$2; iso=$3
    base=$(basename "$iso" .iso)
    st=$(subtree_for "$(basename "$iso")")
    if [ -n "$st" ]; then
        $SUDO "$PY" "$TOOLS/lister.py" "$mp" "$d/listing.json" --subtree "$st" > "$d/lister.log" 2>&1
    else
        $SUDO "$PY" "$TOOLS/lister.py" "$mp" "$d/listing.json" > "$d/lister.log" 2>&1
    fi
    $SUDO chown -R "$(id -u)" "$d" 2>/dev/null
    ls -laR "$mp" > "$d/ls.txt" 2>&1
    if [ -f "$IMGDIR/$base.oracle.json" ] && [ -f "$d/listing.json" ]; then
        "$PY" "$TOOLS/compare.py" "$IMGDIR/$base.oracle.json" "$d/listing.json" > "$d/compare.json"
    fi
}

for iso in "$IMGDIR"/*.iso; do
    base=$(basename "$iso" .iso)
    echo "=== $base"
    case $OS in
    Darwin)
        # 1. Let the system pick the driver.
        d=$OUT/$base@hdiutil; mkdir -p "$d"
        if hdiutil attach -readonly -nobrowse -noverify -noautofsck -plist "$iso" > "$d/attach.plist" 2> "$d/attach.err"; then
            mp=$($PY -c "import plistlib,sys; e=plistlib.load(open(sys.argv[1],'rb'))['system-entities']; print(next((x['mount-point'] for x in e if 'mount-point' in x), ''))" "$d/attach.plist")
            dev=$($PY -c "import plistlib,sys; e=plistlib.load(open(sys.argv[1],'rb'))['system-entities']; print(e[0]['dev-entry'])" "$d/attach.plist")
            if [ -n "$mp" ]; then
                mount | grep " on $mp " > "$d/fstype.txt"
                diskutil info "$mp" > "$d/diskutil.txt" 2>&1
                list_and_compare "$d" "$mp" "$iso"
            else
                echo "attached as $dev but not mounted: $(cat "$d/attach.err")" > "$d/mount-error.txt"
            fi
            hdiutil detach "$dev" > /dev/null 2>&1 || hdiutil detach -force "$dev" > /dev/null 2>&1
        else
            echo "hdiutil attach failed: $(cat "$d/attach.err")" > "$d/mount-error.txt"
        fi
        # 2. mount_cd9660 directly, with option variants.
        dev=$(hdiutil attach -readonly -nomount -noverify -noautofsck "$iso" 2>/dev/null | awk 'NR==1{print $1}')
        for v in default j r; do
            d=$OUT/$base@cd9660-$v; mkdir -p "$d"
            case $v in default) o="";; *) o="-$v";; esac
            if $SUDO mount_cd9660 $o "$dev" $MNT > "$d/mount.log" 2>&1 ||
               $SUDO mount -t cd9660 -o rdonly "$dev" $MNT >> "$d/mount.log" 2>&1; then
                mount | grep " on $MNT " > "$d/fstype.txt"
                echo "options: $o" >> "$d/fstype.txt"
                list_and_compare "$d" $MNT "$iso"
                $SUDO umount $MNT
            else
                echo "mount_cd9660 $o $dev failed: $(cat "$d/mount.log")" > "$d/mount-error.txt"
            fi
        done
        [ -n "$dev" ] && hdiutil detach "$dev" > /dev/null 2>&1
        ;;
    FreeBSD)
        md=$(mdconfig -a -t vnode -o readonly -f "$iso")
        for v in default utf8 nojoliet norr-utf8; do
            d=$OUT/$base@$v; mkdir -p "$d"
            case $v in
            default) o="";;
            utf8) o="-C UTF-8";;
            nojoliet) o="-j";;
            norr-utf8) o="-r -C UTF-8";;
            esac
            if mount_cd9660 $o /dev/$md $MNT > "$d/mount.log" 2>&1; then
                mount | grep " on $MNT " > "$d/fstype.txt"
                echo "options: $o" >> "$d/fstype.txt"
                list_and_compare "$d" $MNT "$iso"
                umount $MNT
            else
                echo "mount_cd9660 $o failed: $(cat "$d/mount.log")" > "$d/mount-error.txt"
            fi
        done
        mdconfig -d -u "$md"
        ;;
    NetBSD|OpenBSD)
        if [ $OS = NetBSD ]; then vndconfig vnd0 "$iso"; parts="a d c"; else vnconfig vnd0 "$iso"; parts="c a"; fi
        for v in default nojoliet norrip; do
            d=$OUT/$base@$v; mkdir -p "$d"
            case $v in default) o="";; *) o="-o $v";; esac
            ok=""
            for p in $parts; do
                if mount_cd9660 $o /dev/vnd0$p $MNT >> "$d/mount.log" 2>&1; then ok=$p; break; fi
            done
            if [ -n "$ok" ]; then
                mount | grep " on $MNT " > "$d/fstype.txt"
                echo "options: $o partition: $ok" >> "$d/fstype.txt"
                list_and_compare "$d" $MNT "$iso"
                umount $MNT
            else
                echo "mount_cd9660 $o failed: $(cat "$d/mount.log")" > "$d/mount-error.txt"
            fi
        done
        if [ $OS = NetBSD ]; then vndconfig -u vnd0; else vnconfig -u vnd0; fi
        ;;
    esac
done

ctl=$(ls -d "$OUT"/control-xorriso@* 2>/dev/null | head -n 1)
if [ -n "$ctl" ] && [ -f "$ctl/listing.json" ]; then
    "$PY" "$TOOLS/compare.py" --selftest "$IMGDIR/control-xorriso.oracle.json" "$ctl/listing.json" | tee "$OUT/selftest.txt"
fi
"$PY" "$TOOLS/summarise.py" "$OUT" | tee "$OUT/summary.txt"
