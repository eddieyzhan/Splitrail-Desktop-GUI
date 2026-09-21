# Splitrail Desktop — AI token usage and cost tracker

**Track token usage and estimated costs for Codex CLI, Claude Code, Gemini CLI and other AI coding tools in one desktop dashboard.** See daily trends and model breakdowns, customize model prices and optionally combine usage from multiple computers through your own private GitHub repository.

The **Codex usage tracker works on its own**. Tracking other tools requires the separate [Splitrail collector](https://github.com/Piebald-AI/splitrail). GitHub sign-in is only needed if you enable device sync.

[Download the app](https://github.com/eddieyzhan/splitrail-desktop/releases/latest/download/splitrail-desktop.pyz) · [Quick start](#quick-start) · [Connect devices](#sync-usage-between-computers-optional) · [Troubleshooting](#troubleshooting) · [Privacy](PRIVACY.md)

![AI token usage dashboard with daily charts and model costs in the Pearl theme; synthetic demo data](docs/pearl.png)

## What you can track

| What you want | What you need |
| --- | --- |
| Codex CLI token usage and cost estimates | The desktop app and existing local Codex logs. No Splitrail collector required. Codex-authenticated Pi logs are also supported. |
| Claude Code, Gemini CLI and other supported tools | [Splitrail 3.9.1 or newer](https://github.com/Piebald-AI/splitrail#installation), available on your PATH. Supported sources depend on the collector. |
| Combined usage across computers | Optional GitHub CLI sign-in and a private repository you control. Each device chooses **All tools** or **Codex only** as its source. |
| Codex quota display | Optional `quota-axi`; missing quota tools do not block token tracking or sync. [Quota details](docs/REFERENCE.md#optional-quota-monitoring). |

Built with Python and Tk: no web server, telemetry or third-party Python package dependencies. **Costs are estimates, not provider bills or subscription charges.** The bundled price catalogue is an offline snapshot, and you can override rates locally.

## Quick start

### 1. Install Python with Tk

You need **Python 3.11+ and Tk 8.6+**. Linux has been tested; Windows and macOS have platform-specific code paths but have not yet been verified on those operating systems.

| Platform | Setup |
| --- | --- |
| Ubuntu / Debian | Install Python 3.11+ and `python3-tk` (`sudo apt install python3-tk`). |
| Fedora | Install Python 3.11+ and `python3-tkinter` (`sudo dnf install python3-tkinter`). |
| Windows | Install Python 3.11+ from [python.org](https://www.python.org/downloads/), including Tcl/Tk and the Python launcher. |
| macOS | Use a Python 3.11+ distribution that includes Tk, such as the [python.org installer](https://www.python.org/downloads/macos/). |

### 2. Download and launch

Download [**splitrail-desktop.pyz**](https://github.com/eddieyzhan/splitrail-desktop/releases/latest/download/splitrail-desktop.pyz). It is a single-file Python application; there is no `pip install` step. Open a terminal in the folder where you saved it.

**Linux / macOS:**

```sh
python3 splitrail-desktop.pyz
```

**Windows PowerShell:**

```powershell
py -3 splitrail-desktop.pyz
```

Release notes and checksums are on the [release page](https://github.com/eddieyzhan/splitrail-desktop/releases/latest).

### 3. Choose your usage view

Select **Use on this device** at first launch. You can connect GitHub later.

- For Codex, choose **Usage → Codex only**. The app reads existing local usage logs.
- For multiple AI coding tools, install the [Splitrail collector](https://github.com/Piebald-AI/splitrail#installation), then choose **Usage → This device** or **All devices**.
- Use **Settings → General → Manage model prices** to inspect or override rates.
- Change between the light **Pearl** and dark **Nord** themes in Settings.

The app defaults to **All devices** when it finds the collector, otherwise **Codex only**. The **All devices** label does not enable network sync by itself.

### Try it with sample data

```sh
python3 splitrail-desktop.pyz --demo
```

On Windows use `py -3` in place of `python3`. Demo mode uses synthetic data, does not read usage logs and cannot connect or sync.

<details>
<summary>See the dark theme and first-run screen</summary>

![Nord dark theme with synthetic AI usage data](docs/nord.png)
![First-run setup offering local use or optional GitHub connection](docs/welcome.png)

</details>

## Sync usage between computers (optional)

Local tracking works without GitHub. To combine usage from your own computers:

1. Install [GitHub CLI](https://cli.github.com/) on each computer.
2. Open **Settings → GitHub sync**. Choose **Sign in with browser**, or **Check connection** if GitHub CLI is already signed in.
3. On the first computer, choose **Create private repository**. On each additional computer, choose **Use existing repository** and enter the same `owner/repository`.
4. Choose **All tools** (requires the collector) or **Codex only**. Review and enable **Allow usage totals and estimated costs to sync**, then click **Connect**.
5. Click **Sync now**, then use **Usage → All devices** or **Codex only** to view combined totals. Automatic sync is off by default; enable **Sync automatically when usage refreshes** if wanted.

Only daily usage totals, estimated costs, dates and approved tool/model identifiers are synced. Prompts, responses, credentials, local paths and account/quota data are excluded. A private GitHub repository is access controlled, not end-to-end encrypted. See [Privacy](PRIVACY.md) and the [sync reference](docs/REFERENCE.md#what-github-sync-transfers).

**Avoid double counting:** keep each computer's source logs separate. Aggregate sync cannot deduplicate the same logs copied to two computers. Do not manually import the same usage you already sync.

**Download only on this device** lets a computer view remote totals without uploading its own. Disconnecting stops sync and removes downloaded totals locally; it does not delete local logs or your private repository. Automatic refresh and sync require the app to remain open.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `No module named tkinter` | Install the Tk package for the Python interpreter you use to launch the app. See the platform table above. |
| No usage appears | Select the correct **Usage** view and confirm the source tool has local logs. For tools other than Codex, install Splitrail 3.9.1+ and make sure `splitrail --version` works. |
| Collector is not found | Check PATH or set `SPLITRAIL_BIN` to its executable. The built-in Codex reader can still work without it. |
| A model has no cost estimate | Open **Notifications**, then add a rate under **Settings → General → Manage model prices**. Tokens remain visible for unknown models. |
| An estimate differs from your bill | Estimates depend on saved usage and configured prices. They cannot reconstruct subscription charges, all service tiers or every billing surcharge. [Pricing details](docs/REFERENCE.md#pricing). |
| GitHub sync fails | Use **Check connection**, confirm repository access and ensure the repository is private. The last successfully downloaded totals are retained after a failed sync. |
| Codex quotas are unavailable | Quota monitoring uses optional tools with their own authentication. It is separate from usage tracking. [Setup details](docs/REFERENCE.md#optional-quota-monitoring). |

For bug reports, include your OS, Python version, app version and the error message. Do not attach raw session logs, credentials or real-account screenshots. See [Privacy](PRIVACY.md).

## More controls

- **Manual Codex export/import:** use **Usage → Export usage / Import usage**, without GitHub. [Export format and deduplication](docs/REFERENCE.md#local-data-and-controls).
- **Custom model prices:** local rate overrides and details about cached/reasoning tokens. [Pricing reference](docs/REFERENCE.md#pricing).
- **CLI commands:** export, import, configure sync and sync once from a terminal. [Command-line reference](docs/REFERENCE.md#command-line).
- **Data locations and refresh behavior:** settings files, log discovery, privacy boundaries and device limits. [Reference guide](docs/REFERENCE.md).

## Run from source or contribute

```sh
git clone https://github.com/eddieyzhan/splitrail-desktop.git
cd splitrail-desktop
python3 splitrail-desktop
```

No third-party Python packages are required. To run the tests and build the downloadable app:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/build.py
python3 dist/splitrail-desktop.pyz --self-check
python3 dist/splitrail-desktop.pyz --smoke-ui
```

The test command above uses POSIX shell syntax. GUI checks need a display or Xvfb. Tests use synthetic data and an in-memory GitHub service; they do not upload real usage. See the [source layout](docs/REFERENCE.md#source-layout).

Licensed under [MIT](LICENSE). Splitrail Desktop is an independent interface for [Splitrail](https://github.com/Piebald-AI/splitrail), not affiliated with AI model providers or GitHub.
