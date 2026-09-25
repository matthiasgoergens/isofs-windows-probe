#!/bin/sh
# Fetch the parts of three archive.org discs needed to list one directory
# each (see tools/partial.py), and compute the oracle for that directory.
# The discs are not stored in this repository.
set -eu
OUT=$1
HERE=$(cd "$(dirname "$0")" && pwd)
T=$HERE/../tools
mkdir -p "$OUT"
fetch() {  # item file size subtree outname
    url="https://archive.org/download/$1/$(printf '%s' "$2" | sed 's/ /%20/g')"
    python3 "$T/partial.py" fetch "$url" "$3" "$4" "$OUT/$5.partial"
    python3 "$T/partial.py" expand "$OUT/$5.partial" "/tmp/$5.iso"
    hash=""
    python3 -c "import json,sys,struct; f=open(sys.argv[1],'rb'); f.read(8); n=struct.unpack('<I',f.read(4))[0]; sys.exit(0 if json.loads(f.read(n))['content_fetched'] else 1)" "$OUT/$5.partial" || hash=--no-hash
    python3 "$T/oracle.py" "/tmp/$5.iso" --subtree "$4" $hash > "$OUT/$5.oracle.json"
    rm -f "/tmp/$5.iso"
}
fetch Anime_Transformer DM_BXL2.iso 674670592 /XTRAS DM_BXL2
fetch comdex-edu Comdex_05.iso 502978560 /IMAGES Comdex_05
fetch itsoftcd-39 "ITSOFTCD 39.iso" 631525376 /PROMOTIONS_SOFTWARE_39/AUTOFLIGHT/DISK13 ITSOFTCD_39
cat > "$OUT/subtrees.json" <<'J'
{"DM_BXL2.iso": "/XTRAS", "Comdex_05.iso": "/IMAGES",
 "ITSOFTCD_39.iso": "/PROMOTIONS_SOFTWARE_39/AUTOFLIGHT/DISK13"}
J
ls -la "$OUT"
