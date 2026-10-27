"""Fail closed on unexpected publication files; never print matched secret bytes."""

from __future__ import annotations

import re
import sys
from pathlib import Path

EXCLUDED = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "dist", "test-results", "playwright-report",
}
ROOT_FILES = {
    ".gitignore", "LICENSE", "README.md", "NOTICE.md", "CONTRIBUTING.md", "SECURITY.md",
    "CODE_OF_CONDUCT.md", "hacs.json", "package.json", "package-lock.json",
    "pyproject.toml", "requirements-dev.txt", "requirements-provisioning.txt",
}
IMAGE_FILES = {
    "custom_components/gwm_thailand_connect/brand/icon.png",
    *(f"docs/images/{name}.png" for name in (
        "material", "method", "code", "password", "consent", "verification",
        "options", "dashboard", "confirmation", "journeys",
    )),
}
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".css", ".html", ".json", ".yaml", ".yml", ".md", ".toml", ".txt"}
RULES = {
    "private-key-block": re.compile(
        rb"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/]{24}",
    ),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "jwt-token": re.compile(rb"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
    "literal-app-secret": re.compile(rb"""["']?app_secret["']?\s*[:=]\s*["'][0-9a-f]{32}["']"""),
    "personal-workstation-path": re.compile(rb"[A-Za-z]:\\Users\\[A-Za-z0-9_-]+", re.I),
}


def payload_issues(data: bytes) -> list[str]:
    return [name for name, pattern in RULES.items() if pattern.search(data)]


def public_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if not (set(path.relative_to(root).parts) & EXCLUDED)
        and (path.is_file() or path.is_symlink())
    )


def scan(root: Path) -> list[Path]:
    files = public_files(root)
    problems: list[str] = []
    for path in files:
        relative = path.relative_to(root)
        name = relative.as_posix()
        allowed_root = len(relative.parts) == 1 and name in ROOT_FILES
        allowed_directory = (
            relative.parts[0] in {"docs", "tests", "scripts", "dashboards", ".github"}
            or relative.parts[:2] == ("custom_components", "gwm_thailand_connect")
            or name == "custom_components/__init__.py"
        )
        if path.is_symlink() or not (allowed_root or allowed_directory):
            problems.append(f"{name}: unexpected path or symlink")
            continue
        if not allowed_root and path.suffix not in TEXT_SUFFIXES and name not in IMAGE_FILES:
            problems.append(f"{name}: unexpected file type")
            continue
        data = path.read_bytes()
        if len(data) > 2 * 1024 * 1024:
            problems.append(f"{name}: oversized publication file")
        if name in IMAGE_FILES:
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                problems.append(f"{name}: not a PNG")
            offset = 8
            while offset + 12 <= len(data):
                size = int.from_bytes(data[offset:offset + 4])
                if data[offset + 4:offset + 8] in {b"tEXt", b"iTXt", b"zTXt", b"eXIf"}:
                    problems.append(f"{name}: unexpected image metadata")
                offset += 12 + size
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{name}: unexpected binary payload")
        problems.extend(f"{name}: {rule}" for rule in payload_issues(data))
    if problems:
        raise ValueError("\n".join(problems))
    return files


def main() -> None:
    try:
        files = scan(Path(__file__).resolve().parents[1])
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
    print(f"Public-tree checks passed: {len(files)} source/documentation files")


if __name__ == "__main__":
    main()
