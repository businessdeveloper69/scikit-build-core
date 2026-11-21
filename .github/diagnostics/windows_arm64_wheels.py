"""Check the scikit-build-core #690 example artifacts on native Windows ARM64."""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import platform
import struct
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path


def pin_backend(example: Path) -> None:
    pyproject = example / "pyproject.toml"
    old = pyproject.read_text(encoding="utf-8")
    original = '["scikit-build-core >=0.10", "nanobind >=1.3.2"]'
    pins = '["scikit-build-core ==1.1.2", "nanobind ==3.1.0"]'
    if old.count(original) != 1:
        raise ValueError("The example's build requirements have changed")
    pyproject.write_text(old.replace(original, pins), encoding="utf-8")


def inspect_wheel(wheel: Path) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        extension, = [n for n in archive.namelist() if n.endswith(".pyd")]
        contents = archive.read(extension)
        metadata, = [n for n in archive.namelist() if n.endswith(".dist-info/WHEEL")]
        tags = [s[5:] for s in archive.read(metadata).decode().splitlines() if s.startswith("Tag: ")]
    if contents[:2] != b"MZ":
        raise ValueError("The extension has no DOS header")
    pe_offset, = struct.unpack_from("<I", contents, 0x3C)
    if contents[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("The extension has no PE signature")
    machine, = struct.unpack_from("<H", contents, pe_offset + 4)
    return {"wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "extension": extension, "pe_machine": hex(machine), "tags": tags}


def test_wheel(wheel: Path, fixture: Path, expected_tag: str) -> dict:
    record = inspect_wheel(wheel)
    with tempfile.TemporaryDirectory() as name:
        directory = Path(name)
        subprocess.run([sys.executable, "-m", "venv", str(directory / "env")], check=True)
        py = directory / "env" / "Scripts" / "python.exe"
        commands = [
            [str(py), "-m", "pip", "install", "--disable-pip-version-check", str(wheel), "pytest==8.4.2"],
            [str(py), "-I", "-c", "from nanobind_example import nanobind_example_ext as ext; print(ext.__file__, ext.add(1, 2)); assert ext.add(1, 2) == 3"],
            [str(py), "-I", "-m", "pytest", "-q", "--import-mode=importlib", str(fixture)],
        ]
        for label, cmd in zip(("install", "import", "pytest"), commands):
            run = subprocess.run(cmd, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(run.stdout, flush=True)
            record[label] = run.returncode
            if label == "install" and run.returncode:
                break
    name = Path(record["extension"]).name
    expected_names = {"nanobind_example_ext" + suffix for suffix in importlib.machinery.EXTENSION_SUFFIXES}
    record["ok"] = (name in expected_names and record["pe_machine"] == "0xaa64"
                    and wheel.name.endswith("-" + expected_tag + ".whl")
                    and record["tags"] == [expected_tag]
                    and all(record.get(label) == 0 for label in ("install", "import", "pytest")))
    return record


def verify(abi: str) -> None:
    host = {"machine": platform.machine(), "platform": sysconfig.get_platform(),
            "version": sys.version, "suffixes": importlib.machinery.EXTENSION_SUFFIXES}
    if host["platform"] != "win-arm64":
        raise RuntimeError(f"Expected native ARM64 Windows, got {host}")
    tag = {"cp311": "cp311-cp311-win_arm64", "cp312": "cp312-abi3-win_arm64"}[abi]
    results = {}
    for mode in ("native", "cross"):
        try:
            directory = Path("artifacts") / f"wheel-{mode}-{abi}"
            wheel, = directory.glob("*.whl")
            results[mode] = test_wheel(wheel.resolve(), Path("example/tests").resolve(), tag)
        except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
            results[mode] = {"ok": False, "error": str(exc)}
    receipt = json.dumps({"host": host, "wheels": results}, indent=2)
    print(receipt, flush=True)
    Path("arm64-results.json").write_text(receipt + "\n", encoding="utf-8")
    if not all(row["ok"] for row in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.argv[1] == "pin":
        pin_backend(Path("example"))
    else:
        verify(sys.argv[1])
