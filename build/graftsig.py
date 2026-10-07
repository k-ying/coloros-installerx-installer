#!/usr/bin/env python3
"""Graft a donor APK's APK Signing Block onto a rebuilt APK.

Why this exists
---------------
On ColorOS 17 (Android 17 / SDK 37), an APK living on a *system* partition is parsed
with skipVerify=true (ApkLiteParseUtils.java:379 -> PARSE_IS_SYSTEM_DIR), so
ParsingPackageUtils.java:3342 calls ApkSignatureVerifier.unsafeGetCertsWithoutVerification(),
which reads the signer certificates straight out of the APK Signing Block and does NOT
verify content digests.  PMS then compares those certificates against the ones cached in
packages.xml for com.android.packageinstaller (PackageManagerServiceUtils.verifySignatures,
which has no isSystem escape hatch and would otherwise throw
INSTALL_FAILED_UPDATE_INCOMPATIBLE -> ReconcileFailure -> installer not registered ->
"There must be exactly one installer" -> permanent bootloop).

Grafting the ORIGINAL system APK's signing block keeps the certificate identical to what
packages.xml already records, while letting us replace the APK contents.

Layout
------
    [local file entries][APK Signing Block][central directory][EOCD][comment]
The signing block is:
    [uint64 size1][id-value pairs...][uint64 size2][16-byte "APK Sig Block 42"]
with size1 == size2 == (length of everything between the two uint64s + 16).
Because the block sits *after* all entries and *before* the central directory, swapping
it only shifts the CD and the EOCD's cd_offset field: every local-header offset, and
therefore all zipalign padding, is preserved byte for byte.
"""
import hashlib
import struct
import sys
import zipfile

MAGIC = b"APK Sig Block 42"


def read_eocd(d):
    i = d.rfind(b"PK\x05\x06")
    if i < 0:
        sys.exit("no EOCD found")
    if d[i - 20 : i - 16] == b"PK\x06\x07":
        sys.exit("zip64 EOCD locator present -- unsupported")
    cd_size, cd_off = struct.unpack("<II", d[i + 12 : i + 20])
    comment_len = struct.unpack("<H", d[i + 20 : i + 22])[0]
    return i, cd_off, cd_size, comment_len


def read_block(d, label):
    eocd, cd_off, cd_size, cl = read_eocd(d)
    if d[cd_off - 16 : cd_off] != MAGIC:
        sys.exit(f"{label}: no APK Signing Block immediately before the central directory")
    size2 = struct.unpack("<Q", d[cd_off - 24 : cd_off - 16])[0]
    # Block total length is size2 + 8 (the leading uint64 repeats the same value),
    # so the block begins at cd_off - size2 - 8.  (Not -16: the trailing uint64 is
    # already counted inside size2 along with the 16-byte magic.)
    start = cd_off - size2 - 8
    size1 = struct.unpack("<Q", d[start : start + 8])[0]
    if size1 != size2:
        sys.exit(f"{label}: block size mismatch size1={size1} size2={size2}")
    ids = []
    pos, end = start + 8, cd_off - 24
    while pos < end:
        plen = struct.unpack("<Q", d[pos : pos + 8])[0]
        pid = struct.unpack("<I", d[pos + 8 : pos + 12])[0]
        ids.append((hex(pid), plen))
        pos += 8 + plen
    return {
        "eocd": eocd,
        "cd_off": cd_off,
        "cd_size": cd_size,
        "comment_len": cl,
        "block_start": start,
        "block": d[start:cd_off],
        "ids": ids,
    }


def entry_offsets(path):
    """Map entry name -> (local header offset, data offset, compress_type, size).

    The data offset must be derived from the LOCAL header's own name/extra lengths.
    (ZipInfo.extra is the *central directory* extra field, which for zipalign-padded
    entries is deliberately different from the local one -- using it reports every
    .so as misaligned even when the archive is correctly 4096-byte aligned.)
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    out = {}
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            h = info.header_offset
            if raw[h : h + 4] != b"PK\x03\x04":
                sys.exit(f"{path}: {info.filename}: no local header at {h}")
            nlen, elen = struct.unpack("<HH", raw[h + 26 : h + 30])
            out[info.filename] = (
                h,
                h + 30 + nlen + elen,
                info.compress_type,
                info.file_size,
            )
    return out


def main(src_path, donor_path, out_path):
    src = open(src_path, "rb").read()
    donor = open(donor_path, "rb").read()
    s = read_block(src, "source")
    d = read_block(donor, "donor")
    print(f"source {src_path}")
    print(f"  cd_off={s['cd_off']} cd_size={s['cd_size']} block_start={s['block_start']} block_len={len(s['block'])} ids={s['ids']}")
    print(f"donor  {donor_path}")
    print(f"  cd_off={d['cd_off']} cd_size={d['cd_size']} block_start={d['block_start']} block_len={len(d['block'])} ids={d['ids']}")

    prefix = src[: s["block_start"]]
    tail = bytearray(src[s["cd_off"] :])
    new_cd_off = len(prefix) + len(d["block"])
    eocd_rel = s["eocd"] - s["cd_off"]
    struct.pack_into("<I", tail, eocd_rel + 16, new_cd_off)
    out = prefix + d["block"] + bytes(tail)
    open(out_path, "wb").write(out)

    # ---- verification ------------------------------------------------------
    check = read_block(out, "output")
    ok = True
    if bytes(d["block"]) != bytes(check["block"]):
        print("FAIL: output signing block differs from donor")
        ok = False
    else:
        print("OK: output signing block is byte-identical to the donor's")
    if check["cd_off"] != new_cd_off:
        print("FAIL: cd_offset not patched correctly")
        ok = False
    before = entry_offsets(src_path)
    after = entry_offsets(out_path)
    if before != after:
        diff = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        print(f"FAIL: entry offsets changed: {sorted(diff)[:8]}")
        ok = False
    else:
        print(f"OK: all {len(after)} entry offsets unchanged (zipalign padding preserved)")
    with zipfile.ZipFile(out_path) as z:
        bad = z.testzip()
        if bad:
            print(f"FAIL: corrupt entry {bad}")
            ok = False
        else:
            print(f"OK: zip CRC check passed for {len(z.namelist())} entries")
    notaligned = [
        n
        for n, (_, doff, ct, _) in after.items()
        if n.endswith(".so") and ct == 0 and doff % 4096 != 0
    ]
    print(f"OK: .so entries not 4096-aligned: {len(notaligned)}" if not notaligned else f"FAIL: not aligned: {notaligned}")
    ok = ok and not notaligned
    h = hashlib.sha256(out).hexdigest()
    print(f"output {out_path}: {len(out)} bytes sha256 {h}")
    if not ok:
        sys.exit("verification failed")


if __name__ == "__main__":
    main(*sys.argv[1:4])
