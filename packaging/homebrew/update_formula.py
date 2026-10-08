#!/usr/bin/env python3
"""Update Homebrew formula with release version and SHA-256 checksums."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


def compute_file_sha256(path: Path) -> str:
    """Compute the SHA-256 hexdigest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_sha256sums_file(path: Path) -> dict[str, str]:
    """Parse a SHA256SUMS.txt file into a dictionary mapping filename to sha256."""
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            checksum, filename = parts[0].strip(), parts[1].strip()
            filename = filename.lstrip("*").strip()
            filename = Path(filename).name
            mapping[filename] = checksum
    return mapping


def get_package_version(repo_root: Path) -> str:
    """Read the package version from src/unical/__init__.py."""
    init_path = repo_root / "src" / "unical" / "__init__.py"
    if init_path.exists():
        match = re.search(
            r'__version__\s*=\s*["\']([^"\']+)["\']',
            init_path.read_text(encoding="utf-8"),
        )
        if match:
            return match.group(1)
    return "1.0.0"


def update_formula_content(
    content: str,
    version: str,
    mac_arm64_sha: str,
    mac_x86_sha: str,
    linux_x86_sha: str,
) -> str:
    """Update version and sha256 checksums in formula content."""
    pattern_version = r'(version\s+)"[^"]+"'
    if not re.search(pattern_version, content):
        raise ValueError("Could not find 'version \"...\"' in formula content")
    content = re.sub(pattern_version, rf'\g<1>"{version}"', content)

    pattern_arm64 = (
        r'(url\s+"[^"]*unical-macos-arm64\.tar\.gz"\s*\n\s*sha256\s+)"[^"]*"'
    )
    if not re.search(pattern_arm64, content):
        raise ValueError("Could not find macos-arm64 URL and sha256 in formula content")
    content = re.sub(pattern_arm64, rf'\g<1>"{mac_arm64_sha}"', content)

    pattern_mac_x86 = (
        r'(url\s+"[^"]*unical-macos-x86_64\.tar\.gz"\s*\n\s*sha256\s+)"[^"]*"'
    )
    if not re.search(pattern_mac_x86, content):
        raise ValueError(
            "Could not find macos-x86_64 URL and sha256 in formula content"
        )
    content = re.sub(pattern_mac_x86, rf'\g<1>"{mac_x86_sha}"', content)

    pattern_linux = (
        r'(url\s+"[^"]*unical-linux-x86_64\.tar\.gz"\s*\n\s*sha256\s+)"[^"]*"'
    )
    if not re.search(pattern_linux, content):
        raise ValueError(
            "Could not find linux-x86_64 URL and sha256 in formula content"
        )
    content = re.sub(pattern_linux, rf'\g<1>"{linux_x86_sha}"', content)

    return content


def update_formula(
    repo_root: Path,
    version: str | None = None,
    release_dir: Path | None = None,
    checksums_file: Path | None = None,
    formula_files: list[Path] | None = None,
    output_formula: Path | None = None,
    mac_arm64_sha: str | None = None,
    mac_x86_sha: str | None = None,
    linux_x86_sha: str | None = None,
) -> None:
    """Update formula files with release checksums and optionally write release formula."""
    if version:
        version = version.lstrip("v").strip()
        if not version or not version[0].isdigit():
            version = get_package_version(repo_root)
    else:
        version = get_package_version(repo_root)

    checksum_map: dict[str, str] = {}
    if checksums_file and checksums_file.exists():
        checksum_map.update(parse_sha256sums_file(checksums_file))
    elif release_dir and (release_dir / "SHA256SUMS.txt").exists():
        checksum_map.update(parse_sha256sums_file(release_dir / "SHA256SUMS.txt"))

    targets = {
        "mac_arm64": ("unical-macos-arm64.tar.gz", mac_arm64_sha),
        "mac_x86": ("unical-macos-x86_64.tar.gz", mac_x86_sha),
        "linux_x86": ("unical-linux-x86_64.tar.gz", linux_x86_sha),
    }

    resolved: dict[str, str] = {}
    for key, (filename, explicit_val) in targets.items():
        if explicit_val:
            resolved[key] = explicit_val
        elif filename in checksum_map:
            resolved[key] = checksum_map[filename]
        elif release_dir and (release_dir / filename).exists():
            resolved[key] = compute_file_sha256(release_dir / filename)
        else:
            raise FileNotFoundError(
                f"Could not determine SHA-256 for {filename}. "
                f"Please ensure {filename} exists in release directory or checksums file."
            )

    mac_arm64_sha_final = resolved["mac_arm64"]
    mac_x86_sha_final = resolved["mac_x86"]
    linux_x86_sha_final = resolved["linux_x86"]

    if formula_files is None:
        formula_files = [
            repo_root / "Formula" / "unical.rb",
            repo_root / "packaging" / "homebrew" / "unical.rb",
        ]

    for fpath in formula_files:
        if fpath.exists():
            old_content = fpath.read_text(encoding="utf-8")
            new_content = update_formula_content(
                old_content,
                version,
                mac_arm64_sha_final,
                mac_x86_sha_final,
                linux_x86_sha_final,
            )
            fpath.write_text(new_content, encoding="utf-8")
            print(f"Updated {fpath} with version {version}")

    if output_formula is not None:
        output_formula.parent.mkdir(parents=True, exist_ok=True)
        primary = (
            formula_files[0] if formula_files else (repo_root / "Formula" / "unical.rb")
        )
        content = primary.read_text(encoding="utf-8")
        output_formula.write_text(content, encoding="utf-8")
        print(f"Wrote release formula to {output_formula}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update Homebrew formula checksums and version."
    )
    parser.add_argument("--version", help="Release version (e.g. 1.0.0 or v1.0.0)")
    parser.add_argument(
        "--release-dir",
        type=Path,
        help="Directory containing release archives and SHA256SUMS.txt",
    )
    parser.add_argument("--checksums-file", type=Path, help="Path to SHA256SUMS.txt")
    parser.add_argument(
        "--formula-files", nargs="*", type=Path, help="Formula files to update"
    )
    parser.add_argument(
        "--output-formula",
        type=Path,
        help="Destination file for release artifact formula",
    )
    parser.add_argument(
        "--mac-arm64-sha", help="Direct SHA-256 for macOS ARM64 archive"
    )
    parser.add_argument("--mac-x86-sha", help="Direct SHA-256 for macOS x86_64 archive")
    parser.add_argument(
        "--linux-x86-sha", help="Direct SHA-256 for Linux x86_64 archive"
    )
    parser.add_argument("--repo-root", type=Path, help="Repository root path")

    args = parser.parse_args(argv)

    repo_root = args.repo_root or Path(__file__).resolve().parent.parent.parent

    try:
        update_formula(
            repo_root=repo_root,
            version=args.version,
            release_dir=args.release_dir,
            checksums_file=args.checksums_file,
            formula_files=args.formula_files,
            output_formula=args.output_formula,
            mac_arm64_sha=args.mac_arm64_sha,
            mac_x86_sha=args.mac_x86_sha,
            linux_x86_sha=args.linux_x86_sha,
        )
        return 0
    except Exception as e:
        print(f"Error updating formula: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
