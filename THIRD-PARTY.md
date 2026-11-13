# Third-party software

FolderEcho source code uses only the Python standard library and has no third-party dependencies.

## In the Windows FolderEcho.exe

The `.exe` is made with PyInstaller. It contains a copy of the Python runtime and the modules of the Python standard library that FolderEcho uses.

| Component | License | Notes |
|---|---|---|
| Python runtime and standard library | PSF License Version 2 (https://docs.python.org/3/license.html) | Copyright (c) Python Software Foundation. Redistribution requires the license text, which is included as PSF-LICENSE.txt in the download zip (source: licenses/PSF-LICENSE.txt, copied from the CPython 3.12 LICENSE file). |
| PyInstaller bootloader | GPL 2.0 with a special exception that allows bundling into programs under any license (https://pyinstaller.org/en/stable/license.html) | Used only to build the file. The exception means FolderEcho itself stays MIT. |
| Microsoft Visual C++ runtime DLLs (vcruntime140.dll and similar) | Microsoft redistribution terms | Python for Windows depends on them and PyInstaller may copy them into the .exe. The exact list printed by the build is checked before release; verify before wider redistribution. |

## Test only (never shipped)

| Component | License | Use |
|---|---|---|
| websocket-client | Apache License 2.0 | Lets the browser test talk to Chrome. Installed only in CI. |
| Google Chrome / Microsoft Edge (already on the runner) | Their own terms | Used by the test; not distributed. |

This list was written by hand. Verify it before relying on it for legal purposes.

