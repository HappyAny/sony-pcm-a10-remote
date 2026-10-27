# Third-party notices

The independently written C# and Python application code is licensed GPL-3.0-only. Sony product names identify the compatible device; this is an unofficial project.

The release includes a native WPF app with Microsoft's official Fluent theme and a private Python 3.14.1 backend built with PyInstaller 6.17.0. The single-file portable edition embeds .NET 10.0.14 Windows Desktop Runtime and the Python backend. The directory edition uses the installed .NET 10 Windows Desktop Runtime. Their original licenses remain applicable:

- Python: PSF License Agreement and included historical licenses, reproduced in `docs/licenses/Python.txt`.
- PyInstaller: GPL-2.0-or-later with the bootloader exception, reproduced in `docs/licenses/PyInstaller.txt`.
- Bundled .NET 10.0.14 runtime and WPF: MIT licenses are reproduced unchanged from the official NuGet runtime packages in `docs/licenses/DotNet-Runtime-10.0.14.txt` and `docs/licenses/DotNet-Desktop-10.0.14.txt`. Runtime notices are in `docs/licenses/DotNet-Runtime-ThirdPartyNotices-10.0.14.txt`. Previously collected Microsoft terms and notices remain in `docs/licenses/DotNet.txt` and `docs/licenses/DotNet-ThirdPartyNotices.txt`.

The single-file app embeds these notices and licenses. Open the gear menu and select 许可证 to read or copy them. The directory edition also carries them in its `licenses/` directory. Tcl/Tk resources retained in the development checkout belong to the legacy 0.1.x interface and keep their original `license.terms` files. They are not included in the native release or its backend.

Sony REC Remote APKs and decompiled source are not distributed, incorporated as code, or included in either executable. The project records protocol facts obtained from local analysis; its own wire encoder was compared against a locally compiled reference only. JADX is a local analysis tool and is not included in release packages.
