"""Tests for packaging configuration and standalone distribution assets."""

import importlib.util
import tomllib
from pathlib import Path
from typing import Any

import pytest

from unical.cli.main import main


def _get_update_formula_module() -> Any:
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "packaging" / "homebrew" / "update_formula.py"
    spec = importlib.util.spec_from_file_location("update_formula", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pyproject_metadata() -> None:
    """Validate pyproject.toml structure, required metadata, and PyPI readiness."""
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_path = repo_root / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml must exist"

    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    assert project.get("name") == "uni-calendar-coloring"
    assert "version" in project.get("dynamic", [])
    assert project.get("description")
    assert project.get("readme") == "README.md"
    assert project.get("requires-python") == ">=3.12"

    authors = project.get("authors", [])
    assert len(authors) > 0
    assert any(a.get("name") == "Davide Bertoni" for a in authors)

    classifiers = project.get("classifiers", [])
    assert "Environment :: Console" in classifiers
    assert "License :: OSI Approved :: MIT License" in classifiers

    keywords = project.get("keywords", [])
    assert "calendar" in keywords

    urls = project.get("urls", {})
    assert "Homepage" in urls
    assert "Repository" in urls
    assert "Issues" in urls

    scripts = project.get("scripts", {})
    assert scripts.get("unical") == "unical.cli.main:main"


def test_entrypoint_callable() -> None:
    """Ensure the entry point function exists and is callable."""
    assert callable(main)


def test_main_module_file() -> None:
    """Ensure src/unical/__main__.py exists and invokes main."""
    repo_root = Path(__file__).resolve().parent.parent
    main_file = repo_root / "src" / "unical" / "__main__.py"
    assert main_file.exists()
    content = main_file.read_text(encoding="utf-8")
    assert "from unical.cli.main import main" in content
    assert "main()" in content


def test_pyinstaller_spec_file() -> None:
    """Ensure packaging/unical.spec exists and references entrypoint and assets."""
    repo_root = Path(__file__).resolve().parent.parent
    spec_file = repo_root / "packaging" / "unical.spec"
    assert spec_file.exists(), "packaging/unical.spec must exist"
    content = spec_file.read_text(encoding="utf-8")
    assert "__main__.py" in content
    assert 'name="unical"' in content
    assert "textual" in content
    assert "googleapiclient" in content
    assert "tzdata" in content
    assert "platformdirs" in content


def test_homebrew_formula_files() -> None:
    """Ensure Homebrew formula files exist and define the Unical formula."""
    repo_root = Path(__file__).resolve().parent.parent
    formula_files = [
        repo_root / "Formula" / "unical.rb",
        repo_root / "packaging" / "homebrew" / "unical.rb",
    ]
    for formula in formula_files:
        assert formula.exists(), f"{formula} must exist"
        content = formula.read_text(encoding="utf-8")
        assert "class Unical < Formula" in content
        assert "bin.install" in content
        assert "unical --help" in content


def test_update_formula_content_success() -> None:
    """Ensure update_formula_content updates version and checksums."""
    mod = _get_update_formula_module()
    sample_content = (
        "class Unical < Formula\n"
        '  version "0.1.0"\n'
        "  on_macos do\n"
        "    if Hardware::CPU.arm?\n"
        '      url "https://github.com/.../unical-macos-arm64.tar.gz"\n'
        '      sha256 "PLACEHOLDER_MAC_ARM64_SHA256"\n'
        "    else\n"
        '      url "https://github.com/.../unical-macos-x86_64.tar.gz"\n'
        '      sha256 "PLACEHOLDER_MAC_X86_64_SHA256"\n'
        "    end\n"
        "  end\n"
        "  on_linux do\n"
        '    url "https://github.com/.../unical-linux-x86_64.tar.gz"\n'
        '    sha256 "PLACEHOLDER_LINUX_X86_64_SHA256"\n'
        "  end\n"
        "end\n"
    )
    arm64_sha = "a" * 64
    x86_sha = "b" * 64
    linux_sha = "c" * 64

    updated = mod.update_formula_content(
        sample_content,
        version="1.2.3",
        mac_arm64_sha=arm64_sha,
        mac_x86_sha=x86_sha,
        linux_x86_sha=linux_sha,
    )
    assert 'version "1.2.3"' in updated
    assert f'sha256 "{arm64_sha}"' in updated
    assert f'sha256 "{x86_sha}"' in updated
    assert f'sha256 "{linux_sha}"' in updated


def test_update_formula_content_missing_pattern_raises() -> None:
    """Ensure update_formula_content raises ValueError on invalid formula structure."""
    mod = _get_update_formula_module()
    invalid_content = "class Unical < Formula\nend\n"
    with pytest.raises(ValueError, match="version"):
        mod.update_formula_content(
            invalid_content, "1.0.0", "a" * 64, "b" * 64, "c" * 64
        )


def test_parse_sha256sums_file(tmp_path: Path) -> None:
    """Ensure parse_sha256sums_file correctly parses hash files."""
    mod = _get_update_formula_module()
    sums_file = tmp_path / "SHA256SUMS.txt"
    sums_file.write_text(
        "# Comment\n\n"
        "1111111111111111111111111111111111111111111111111111111111111111  unical-macos-arm64.tar.gz\n"
        "2222222222222222222222222222222222222222222222222222222222222222 *unical-macos-x86_64.tar.gz\n"
        "3333333333333333333333333333333333333333333333333333333333333333  release/unical-linux-x86_64.tar.gz\n",
        encoding="utf-8",
    )
    mapping = mod.parse_sha256sums_file(sums_file)
    assert mapping["unical-macos-arm64.tar.gz"] == "1" * 64
    assert mapping["unical-macos-x86_64.tar.gz"] == "2" * 64
    assert mapping["unical-linux-x86_64.tar.gz"] == "3" * 64


def test_update_formula_end_to_end(tmp_path: Path) -> None:
    """Ensure update_formula processes files, reads checksums, and outputs formula."""
    mod = _get_update_formula_module()
    repo_root = tmp_path / "repo"
    formula_dir = repo_root / "Formula"
    pkg_dir = repo_root / "packaging" / "homebrew"
    formula_dir.mkdir(parents=True)
    pkg_dir.mkdir(parents=True)

    template = (
        "class Unical < Formula\n"
        '  version "0.0.1"\n'
        "  on_macos do\n"
        "    if Hardware::CPU.arm?\n"
        '      url "https://github.com/.../unical-macos-arm64.tar.gz"\n'
        '      sha256 "old_arm"\n'
        "    else\n"
        '      url "https://github.com/.../unical-macos-x86_64.tar.gz"\n'
        '      sha256 "old_x86"\n'
        "    end\n"
        "  end\n"
        "  on_linux do\n"
        '    url "https://github.com/.../unical-linux-x86_64.tar.gz"\n'
        '    sha256 "old_linux"\n'
        "  end\n"
        "end\n"
    )
    formula1 = formula_dir / "unical.rb"
    formula2 = pkg_dir / "unical.rb"
    formula1.write_text(template, encoding="utf-8")
    formula2.write_text(template, encoding="utf-8")

    release_dir = tmp_path / "release"
    release_dir.mkdir()
    arm_file = release_dir / "unical-macos-arm64.tar.gz"
    arm_file.write_bytes(b"arm-content")
    x86_file = release_dir / "unical-macos-x86_64.tar.gz"
    x86_file.write_bytes(b"x86-content")
    linux_file = release_dir / "unical-linux-x86_64.tar.gz"
    linux_file.write_bytes(b"linux-content")

    out_file = release_dir / "unical.rb"

    mod.update_formula(
        repo_root=repo_root,
        version="v2.3.4",
        release_dir=release_dir,
        formula_files=[formula1, formula2],
        output_formula=out_file,
    )

    arm_sha = mod.compute_file_sha256(arm_file)
    x86_sha = mod.compute_file_sha256(x86_file)
    linux_sha = mod.compute_file_sha256(linux_file)

    for fpath in [formula1, formula2, out_file]:
        assert fpath.exists()
        content = fpath.read_text(encoding="utf-8")
        assert 'version "2.3.4"' in content
        assert f'sha256 "{arm_sha}"' in content
        assert f'sha256 "{x86_sha}"' in content
        assert f'sha256 "{linux_sha}"' in content


def test_update_formula_cli_main(tmp_path: Path) -> None:
    """Ensure update_formula CLI main function runs cleanly."""
    mod = _get_update_formula_module()
    formula_file = tmp_path / "unical.rb"
    formula_file.write_text(
        "class Unical < Formula\n"
        '  version "0.0.1"\n'
        "  on_macos do\n"
        "    if Hardware::CPU.arm?\n"
        '      url "https://github.com/.../unical-macos-arm64.tar.gz"\n'
        '      sha256 "old_arm"\n'
        "    else\n"
        '      url "https://github.com/.../unical-macos-x86_64.tar.gz"\n'
        '      sha256 "old_x86"\n'
        "    end\n"
        "  end\n"
        "  on_linux do\n"
        '    url "https://github.com/.../unical-linux-x86_64.tar.gz"\n'
        '    sha256 "old_linux"\n'
        "  end\n"
        "end\n",
        encoding="utf-8",
    )
    code = mod.main(
        [
            "--version",
            "1.2.3",
            "--mac-arm64-sha",
            "a" * 64,
            "--mac-x86-sha",
            "b" * 64,
            "--linux-x86-sha",
            "c" * 64,
            "--formula-files",
            str(formula_file),
        ]
    )
    assert code == 0
    content = formula_file.read_text(encoding="utf-8")
    assert 'version "1.2.3"' in content
    assert f'sha256 "{"a" * 64}"' in content

    # Error code returned on failure
    bad_code = mod.main(["--formula-files", "/nonexistent/formula.rb"])
    assert bad_code == 1
