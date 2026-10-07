#!/usr/bin/env python3
"""Rewrite the package name stored in a binary resources.arsc.

Why this is needed
------------------
The upstream InstallerX Revived APK ships as `com.rosan.installer.x.revived`.
To act as the system installer it has to be `com.android.packageinstaller`, and
that name appears in *two* places:

  * the `package=` attribute of AndroidManifest.xml  (handled by patch_sys.py)
  * the `ResTable_package` chunk of resources.arsc   (handled here)

aapt2's `--rename-manifest-package` rewrites both for the same reason.  We cannot
use it, because the build deliberately keeps the *original* resources.arsc byte
for byte and only swaps the manifest -- rebuilding resources.arsc through apktool
would perturb far more than the package name.

Why a byte patch is safe
------------------------
`ResTable_package::name` is a fixed `char16_t name[128]` field -- 256 bytes,
NUL-terminated -- so a shorter name drops straight in and everything else in the
file keeps its offset.  No string pool entry moves, no resource ID changes, and
resolution by ID (which is what the framework does) is completely unaffected.

    ResTable_header   u16 type=0x0002 u16 headerSize u32 size
    ResTable_package  u16 type=0x0200 u16 headerSize u32 size
                      u32 id
                      char16 name[128]        <-- rewritten here
                      u32 typeStrings ...

Usage:
    patch_arsc.py <in.apk|in.arsc> <out.arsc> <new.package.name>

Exits non-zero (without writing the output) if the file does not look like a
resource table or the name does not fit.
"""
import struct
import sys
import zipfile

RES_TABLE_TYPE = 0x0002
RES_TABLE_PACKAGE_TYPE = 0x0200
NAME_FIELD_BYTES = 256  # char16_t name[128]


def load(path):
    if path.lower().endswith(".apk"):
        with zipfile.ZipFile(path) as z:
            return bytearray(z.read("resources.arsc"))
    with open(path, "rb") as fh:
        return bytearray(fh.read())


def decode_name(raw):
    return raw.decode("utf-16-le").split("\x00", 1)[0]


def main(src, out, new_name):
    d = load(src)

    typ, hdr_size, total = struct.unpack_from("<HHI", d, 0)
    if typ != RES_TABLE_TYPE:
        sys.exit(f"{src}: not a resource table (chunk type 0x{typ:04x})")
    if total != len(d):
        sys.exit(f"{src}: header size {total} != file size {len(d)}")

    # Walk the top-level chunks that follow the table header.
    off, packages = hdr_size, []
    while off + 8 <= len(d):
        ctype, chdr, csize = struct.unpack_from("<HHI", d, off)
        if csize < chdr or off + csize > len(d):
            break
        if ctype == RES_TABLE_PACKAGE_TYPE:
            packages.append((off, chdr, csize))
        off += csize

    if not packages:
        sys.exit(f"{src}: no ResTable_package chunk found")

    off, chdr, csize = packages[0]
    if chdr < 12 + NAME_FIELD_BYTES:
        sys.exit(f"{src}: package chunk header too small ({chdr})")

    name_off = off + 12
    old_name = decode_name(bytes(d[name_off : name_off + NAME_FIELD_BYTES]))

    encoded = new_name.encode("utf-16-le")
    if len(encoded) + 2 > NAME_FIELD_BYTES:
        sys.exit(f"new name too long for the 128-char field: {new_name}")

    before = bytes(d)
    d[name_off : name_off + NAME_FIELD_BYTES] = encoded + b"\x00" * (NAME_FIELD_BYTES - len(encoded))

    # --- verification -------------------------------------------------------
    if len(d) != len(before):
        sys.exit("FAIL: file length changed")
    diff = [i for i in range(len(before)) if before[i] != d[i]]
    if not diff or min(diff) < name_off or max(diff) >= name_off + NAME_FIELD_BYTES:
        sys.exit("FAIL: bytes changed outside the name field")
    now = decode_name(bytes(d[name_off : name_off + NAME_FIELD_BYTES]))
    if now != new_name:
        sys.exit(f"FAIL: readback gave {now!r}")

    with open(out, "wb") as fh:
        fh.write(bytes(d))

    print(f"resources.arsc package name: {old_name} -> {now}")
    print(f"  {len(diff)} bytes rewritten inside [{name_off},{name_off + NAME_FIELD_BYTES}) -- nothing else moved")
    if len(packages) > 1:
        print(f"  note: {len(packages)} package chunks present; only the first was renamed")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
