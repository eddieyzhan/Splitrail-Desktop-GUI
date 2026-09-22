# Install Splitrail Desktop

Get files only from the [official releases](https://github.com/eddieyzhan/Splitrail-Desktop-GUI/releases/latest). Native downloads include Python, Qt and the app. No account is needed for local use.

| Computer | Download | Install and open |
| --- | --- | --- |
| Windows 10/11, 64-bit Intel/AMD | `splitrail-desktop-windows-x86_64.zip` | Right-click the ZIP → **Extract All**. Open `Splitrail`, then double-click `Splitrail.exe`. Keep the whole folder together. |
| Mac with Apple silicon (M1 or newer), macOS 14+ | `splitrail-desktop-macos-arm64.zip` | Unzip, drag `Splitrail.app` to **Applications**, then open it. |
| Linux, 64-bit Intel/AMD (Ubuntu 22.04+ or compatible) | `splitrail-desktop-linux-x86_64.tar.gz` | Extract the archive, then run `./Splitrail/Splitrail` from the extracted folder. Keep the whole folder together. |
| Other supported Python/Qt platforms, including Intel Macs | `splitrail-desktop.pyz` | Use the Python instructions below. Intel macOS and Windows ARM are not native-tested release targets. |

Native apps are unsigned (the Mac bundle has only a local ad-hoc signature), and the Mac app is not notarized. Windows may show **More info → Run anyway**. On macOS, first try opening the app, then use **System Settings → Privacy & Security → Open Anyway** if it is blocked. Only approve the app if it came from the official release and its checksum matches. Do not disable OS security globally.

On Ubuntu/Debian, if Qt reports missing X11 libraries, install `libegl1 libopengl0 libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-keysyms1` with your package manager. A graphical desktop is required. The native Linux build needs glibc 2.35 or newer; older distributions should use a compatible Python/Qt installation.

## Python download (all three operating systems)

Install Python 3.11–3.14 from [python.org](https://www.python.org/downloads/) or your OS package manager. Download `splitrail-desktop.pyz` into an empty folder and open a terminal there.

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install "PySide6==6.11.2" "ijson==3.5.1"
.venv\Scripts\python splitrail-desktop.pyz
```

**Linux / macOS:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install "PySide6==6.11.2" "ijson==3.5.1"
.venv/bin/python splitrail-desktop.pyz
```

Run the last command again to reopen the app. No environment activation is necessary. Add `--demo` to try synthetic data without accessing any account or usage logs.

## Verify a download

Download `SHA256SUMS.txt` from the same release. Compare the hash for your file:

```powershell
# Windows
Get-FileHash .\splitrail-desktop-windows-x86_64.zip -Algorithm SHA256
```

```sh
# Linux: verifies files you downloaded; ignores other platform files
sha256sum --check --ignore-missing SHA256SUMS.txt
# macOS: compare with the matching line in SHA256SUMS.txt
shasum -a 256 splitrail-desktop-macos-arm64.zip
```

## First launch, updates and removal

Choose **Use on this device** to track local Codex usage. Other AI tools need the optional [Splitrail collector](https://github.com/Piebald-AI/splitrail#installation). Device sync needs GitHub CLI and an explicitly selected private repository; it is off by default.

To update, quit the app and extract the new download into a fresh folder (or replace the Mac app). Settings and usage remain in the app's OS-specific data directory. To uninstall, delete the extracted app folder or Mac app. See the [reference guide](https://github.com/eddieyzhan/Splitrail-Desktop-GUI/blob/main/docs/REFERENCE.md) for data locations.
