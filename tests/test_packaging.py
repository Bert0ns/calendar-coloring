"""Tests for packaging configuration and standalone distribution assets."""

import tomllib
from pathlib import Path

from unical.cli.main import main


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
