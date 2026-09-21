# Splitrail Desktop GUI

**Track token usage and estimated costs for Codex CLI, Claude Code, Gemini CLI and other AI coding tools in one desktop dashboard.** See daily trends and model breakdowns, customize model prices and optionally combine usage from multiple computers through your own private GitHub repository.

The **Codex usage tracker works on its own**. Tracking other tools requires the separate [Splitrail collector](https://github.com/Piebald-AI/splitrail). GitHub sign-in is only needed if you enable device sync.

[Download the app](https://github.com/eddieyzhan/Splitrail-Desktop-GUI/releases/latest/download/splitrail-desktop.pyz) · [Quick start](#quick-start) · [Connect devices](#sync-usage-between-computers-optional) · [Troubleshooting](#troubleshooting) · [Privacy](PRIVACY.md)

![AI token usage dashboard with daily charts and model costs in the Pearl theme; synthetic demo data](docs/pearl.png)

## What you can track

| What you want | What you need |
| --- | --- |
| Codex CLI token usage and cost estimates | The desktop app and existing local Codex logs. No Splitrail collector required. Codex-authenticated Pi logs are also supported. |
| Claude Code, Gemini CLI and other supported tools | [Splitrail 3.9.1 or newer](https://github.com/Piebald-AI/splitrail#installation), available on your PATH. Supported sources depend on the collector. |
| Combined usage across computers | Optional GitHub CLI sign-in and a private repository you control. Each device chooses **All tools** or **Codex** as its source. |
| Codex quota display | Optional `quota-axi`; missing quota tools do not block token tracking or sync. [Quota details](docs/REFERENCE.md#optional-quota-monitoring). |

Built with Python and Qt Quick, with soft Pearl and Nord themes. No web server or telemetry. **Costs are estimates, not provider bills or subscription charges.** The bundled price catalogue is an offline snapshot, and you can override rates locally.

## Quick start

### 1. Install Python and dependencies

You need **Python 3.11+** and **PySide6 6.8+ (Qt 6)** plus **ijson 3.4+** for streaming usage records. Linux has been tested; Windows and macOS have platform-specific code paths but have not yet been verified on those operating systems.

Create an isolated environment and install the dependencies:

**Linux / macOS:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install "PySide6>=6.8,<7" "ijson>=3.4,<4"
```

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install "PySide6>=6.8,<7" "ijson>=3.4,<4"
```

PySide6 supplies Qt; Tk is no longer required. See [Qt for Python setup](https://doc.qt.io/qtforpython-6/gettingstarted.html) if your platform needs additional libraries.

### 2. Download and launch

Download [**splitrail-desktop.pyz**](https://github.com/eddieyzhan/Splitrail-Desktop-GUI/releases/latest/download/splitrail-desktop.pyz) into the same folder as your environment. It contains the application code and interface; the UI and streaming dependencies are installed separately above.

**Linux / macOS:**

```sh
.venv/bin/python splitrail-desktop.pyz
```

**Windows PowerShell:**

```powershell
.venv\Scripts\python splitrail-desktop.pyz
```

Release notes and checksums are on the [release page](https://github.com/eddieyzhan/Splitrail-Desktop-GUI/releases/latest).

### 3. Choose your usage view

First launch shows only the guided setup. Choose **Get started** to connect devices, or **Use on this device** to go straight to the dashboard. GitHub is optional and can be connected later in Settings.

- For Codex, choose **Settings → Usage & data → Codex**. The app reads existing local usage logs.
- For multiple AI coding tools, install the [Splitrail collector](https://github.com/Piebald-AI/splitrail#installation), then choose **Settings → Usage & data → This device** or **All devices**.
- Use **Settings → Model pricing → Manage prices** to inspect or override rates.
- Change between the light **Pearl** and dark **Nord** themes in Settings.

The app defaults to **All devices** when it finds the collector, otherwise **Codex**. The **All devices** label does not enable network sync by itself.

Select Today, Week, Month, Year or All time directly above the dashboard. The arrows (or Alt+Left/Right) cycle through these five presets and wrap at either end. Use the calendar button for a custom range. Today and other single-day ranges show tokens and costs by hour; longer ranges show daily or grouped trends. **Models**, **Tools** and **History** offer searchable tables. Detailed token totals are under **Models**; pricing and refresh issues are in the notification bell.

### Try it with sample data

```sh
.venv/bin/python splitrail-desktop.pyz --demo
```

On Windows use `.venv\Scripts\python` in place of `.venv/bin/python`. Demo mode uses synthetic data, does not read usage logs and cannot connect or sync.

<details>
<summary>See the dark theme and first-run screen</summary>

![Nord dark theme with synthetic AI usage data](docs/nord.png)
![First-run setup offering local use or optional GitHub connection](docs/welcome.png)

</details>

## Sync usage between computers (optional)

Local tracking works without GitHub. To combine usage from your own computers:

1. Install [GitHub CLI](https://cli.github.com/) on each device.
2. Choose **Get started** during setup, or **Settings → GitHub sync → Connect**. Continue with the detected account, or choose **Sign in with GitHub**. Open GitHub and enter the one-time code shown in the app.
3. On the first device, choose **Create new** and name your private repository, such as `splitrail-usage`. On additional devices, choose **Use existing** and enter the same `owner/repository`.
4. Choose whether to sync automatically, then select **Enable private sync**. This is the explicit step that creates/connects the private repository and enables sharing. Automatic sync is off by default.
5. Open the dashboard. Choose **Settings → GitHub sync → Sync now** for a manual transfer. Under **Options**, select **All tools** (requires Splitrail) or **Codex** as this device's source.


Only daily and hourly usage totals, estimated costs, dates and approved tool/model identifiers are synced. Prompts, responses, credentials, local paths and account/quota data are excluded. A private GitHub repository is access controlled, not end-to-end encrypted. See [Privacy](PRIVACY.md) and the [sync reference](docs/REFERENCE.md#what-github-sync-transfers).

**Avoid double counting:** keep each computer's source logs separate. Aggregate sync cannot deduplicate the same logs copied to two computers. Do not manually import the same usage you already sync.

**Download only** under **Settings → GitHub sync → Options** lets a computer view remote totals without uploading its own. Disconnecting stops sync and removes downloaded totals locally; it does not delete local logs or your private repository. Automatic refresh and sync require the app to remain open.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `No module named PySide6` or `ijson` | Use the environment’s Python from Quick start. Install both dependencies into that same environment. |
| No usage appears | Select the correct source in **Settings → Usage & data** and confirm the source tool has local logs. For tools other than Codex, install Splitrail 3.9.1+ and make sure `splitrail --version` works. |
| Collector is not found | Check PATH or set `SPLITRAIL_BIN` to its executable. The built-in Codex reader can still work without it. |
| A model has no cost estimate | Open **Notifications**, then add a rate under **Settings → Model pricing → Manage prices**. Tokens remain visible for unknown models. |
| An estimate differs from your bill | Estimates depend on saved usage and configured prices. They cannot reconstruct subscription charges, all service tiers or every billing surcharge. [Pricing details](docs/REFERENCE.md#pricing). |
| GitHub sync fails | Confirm GitHub CLI is signed in (`gh auth status`), check repository access and ensure the repository is private. The last successfully downloaded totals are retained after a failed sync. |
| Codex quotas are unavailable | Quota monitoring uses optional tools with their own authentication. It is separate from usage tracking. [Setup details](docs/REFERENCE.md#optional-quota-monitoring). |

For bug reports, include your OS, Python version, app version and the error message. Do not attach raw session logs, credentials or real-account screenshots. See [Privacy](PRIVACY.md).

## More controls

- **Manual Codex export/import:** use **Settings → Usage & data → Export / Import**, without GitHub. [Export format and deduplication](docs/REFERENCE.md#local-data-and-controls).
- **Custom model prices:** local rate overrides and details about cached/reasoning tokens. [Pricing reference](docs/REFERENCE.md#pricing).
- **CLI commands:** export, import, configure sync and sync once from a terminal. [Command-line reference](docs/REFERENCE.md#command-line).
- **Data locations and refresh behavior:** settings files, log discovery, privacy boundaries and device limits. [Reference guide](docs/REFERENCE.md).

## Run from source or contribute

```sh
git clone https://github.com/eddieyzhan/Splitrail-Desktop-GUI.git
cd Splitrail-Desktop-GUI
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
splitrail-desktop
```

The source install includes PySide6, ijson and the QML interface resources. After activating the environment, run the tests and build the downloadable app:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/build.py
python3 dist/splitrail-desktop.pyz --self-check
python3 dist/splitrail-desktop.pyz --smoke-ui
```

The test command above uses POSIX shell syntax. UI tests use the offscreen Qt platform when no display is available. Use a real display or Xvfb for visual smoke checks. Add `--onboarding` to preview setup, or `--demo --onboarding` for an isolated preview. Tests use synthetic data and an in-memory GitHub service; they do not upload real usage. See the [source layout](docs/REFERENCE.md#source-layout).

Application code is licensed under [MIT](LICENSE). [PySide6/Qt](https://doc.qt.io/qtforpython-6/licenses.html) and [ijson](https://github.com/ICRAR/ijson) are separately installed dependencies with their own license terms; neither is bundled in the `.pyz`. Splitrail Desktop is an independent interface for [Splitrail](https://github.com/Piebald-AI/splitrail), not affiliated with AI model providers or GitHub.
