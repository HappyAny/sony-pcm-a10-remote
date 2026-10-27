"""Compare Python framing with locally decompiled Sony framing, for research only.

Requires a privately supplied/decompiled REC Remote APK in .research/decompiled.
Neither the APK nor Sony source is included in the release archive.
"""
from pathlib import Path
import json
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from pcma10_remote.protocol import DATA, Frame, encode

source = root / ".research/decompiled/sources"
work = root / ".research/reference-java"
(work / "b1").mkdir(parents=True, exist_ok=True)
(work / "b1/a.java").write_text("package b1; public class a { public static void p(Exception e) { throw new RuntimeException(e); } }", encoding="utf-8")
(work / "ReferenceVectors.java").write_text('''
import java.util.HexFormat;
public class ReferenceVectors {
 public static void main(String[] args) {
  for (String entry : args) {
   String[] parts = entry.split(":");
   byte[] payload = HexFormat.of().parseHex(parts[1]);
   T0.c encoder = new T0.c();
   encoder.a(payload, payload.length, (byte)Integer.parseInt(parts[0]));
   System.out.println(HexFormat.of().formatHex(encoder.f922a.toByteArray()));
  }
 }
}
''', encoding="utf-8")
java_root = Path(shutil.which("java")).resolve().parent
javac = shutil.which("javac") or str(java_root / "javac.exe")
java = shutil.which("java")
subprocess.run([javac, "-encoding", "UTF-8", "-d", str(work), str(source / "T0/c.java"),
                str(source / "T0/e.java"), str(work / "b1/a.java"), str(work / "ReferenceVectors.java")], check=True)
samples = [(0, b"\x10\x03"), (1, b"\x10\x02"), (0, b"\x10\x01"),
           (0, b"\x01\x01"), (0, bytes.fromhex("3c3d3e")), (1, bytes(range(256)))]
result = subprocess.check_output([java, "-cp", str(work), "ReferenceVectors"] +
                                 [f"{seq}:{payload.hex()}" for seq, payload in samples], text=True)
reference = result.strip().splitlines()
assert len(reference) == len(samples)
for (seq, payload), actual in zip(samples, reference):
    assert encode(Frame(DATA, seq, payload)).hex() == actual
receipt = {"reference": "REC Remote 3.5.0 framing class T0.c and checksum T0.e",
           "vectors_compared": len(samples), "all_match": True,
           "includes_all_byte_values": True, "hardware_capture": False}
(root / "docs").mkdir(exist_ok=True)
(root / "docs/reference-validation.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
print(json.dumps(receipt, indent=2))
