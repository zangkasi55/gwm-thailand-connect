"""Build a deterministic source-only HA install archive after publication checks."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from check_public_tree import scan


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    files = scan(root)
    version = json.loads(
        (root / "custom_components" / "gwm_thailand_connect" / "manifest.json").read_text(encoding="utf-8"),
    )["version"]
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive_path = output / f"gwm-thailand-connect-{version}.zip"
    selected = [
        path for path in files
        if path.relative_to(root).parts[:2] == ("custom_components", "gwm_thailand_connect")
        or path.relative_to(root).as_posix() in {"LICENSE", "NOTICE.md", "hacs.json", "dashboards/gwm-thailand.yaml"}
    ]
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in selected:
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(selected):
            raise ValueError("Release archive verification failed")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    with (output / "SHA256SUMS").open("x", encoding="ascii", newline="\n") as checksums:
        checksums.write(f"{digest}  {archive_path.name}\n")
    print(f"Built {archive_path.name}: {len(selected)} files; SHA-256 {digest}")


if __name__ == "__main__":
    main()
