"""Package the current portable release and its complete application source."""
from pathlib import Path
import hashlib
import zipfile
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from pcma10_remote import __version__
paths = [root / name for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
                                 "pyproject.toml", "pcma10.py", "backend.py", "Launch.cmd", "Demo.cmd")]
excluded_parts = {"__pycache__", "bin", "obj", "logs", ".vs"}
binary_folder = 'dist/standalone' if (root / 'dist/standalone/PCM-A10.exe').is_file() else 'dist/fluent'
for name in ("src", "tests", "scripts", "docs", "desktop", binary_folder):
    paths.extend(p for p in (root / name).rglob("*") if p.is_file()
                 and not excluded_parts.intersection(p.relative_to(root).parts)
                 and p.suffix not in (".pyc", ".pdb")
                 and p.name not in ("self-test.json", "self-test-error.txt", "lifetime-probe.json")
                 and not (name.startswith("dist/") and p.name.startswith("preview-")))
paths = sorted(set(paths))
archive = root / f"PCM-A10-Remote-{__version__}-windows-x64.zip"
manifest = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n" for p in paths)
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for path in paths:
        z.write(path, path.relative_to(root).as_posix())
    z.writestr("SHA256SUMS", manifest)
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert not any(n.startswith((".research/", "build/", "logs/")) for n in z.namelist())
    assert "Launch.cmd" in z.namelist() and "src/pcma10_remote/session.py" in z.namelist()
    assert "desktop/PcmA10.Fluent/MainWindow.xaml.cs" in z.namelist()
    assert ("dist/standalone/PCM-A10.exe" if binary_folder == 'dist/standalone' else "dist/fluent/PCM-A10-Fluent.exe") in z.namelist()
    assert not any("_tkinter" in n.lower() or n.startswith("vendor/") for n in z.namelist())
    for path in paths:
        name = path.relative_to(root).as_posix()
        assert hashlib.sha256(z.read(name)).digest() == hashlib.sha256(path.read_bytes()).digest(), name
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
print(f"{archive}\n{archive.stat().st_size} bytes; {len(paths)} files\nSHA256 {checksum}")
