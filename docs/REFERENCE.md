# Splitrail Desktop GUI Reference

[Back to the quick start](../README.md) · [Privacy](../PRIVACY.md)

## What GitHub sync transfers

Each device gets a random identifier, independent of your hostname or account. It writes its own `usage/<random-id>.json` using the [GitHub Contents API](https://docs.github.com/en/rest/repos/contents). Only dates, hour buckets, known model/tool names, token counts, activity counts, and estimated USD costs are serialized. Unrecognized model/tool names are replaced with stable pseudonyms. No prompts, responses, paths, account/quota fields, credentials, diagnostic text, or raw session IDs are sent.

All devices can upload and download. Repeat syncs replace the same device snapshot; unchanged usage does not create another commit. The app uses the repository's default branch, verifies that it is private before transfers, and never creates workflows. Usage commits use a generic identity and `[skip ci]`. No personal Git author configuration is used by the sync transport.

Downloaded snapshots are validated before one atomic cache replacement. Offline or malformed responses preserve the last good totals. Removing a device file from the private repository removes its contribution after the next successful sync.

**Accounting boundary:** sync adds daily aggregates from distinct devices and preserves each sender's cost estimates. It cannot deduplicate copied session logs between devices because the all-tools collector does not provide request identities. Keep each device's source history distinct; do not also manually import the same usage that you sync. Manual Codex imports deduplicate against available local Codex/Pi request identities, independently of aggregate sync. Moving an entire app data directory to another computer also copies the device identity; remove `sync-device.json` and reconnect on the new computer before syncing.

The explicit **Codex** dashboard includes Codex rows from remote snapshots; **All devices** includes all received tools. Deleted local logs can reduce a later device snapshot. Preserve original logs if you need complete history. The current limits are 100 device files and 8 MB per snapshot. Larger histories need manual export or a future storage backend.

Hourly buckets use each source device’s local calendar date and clock hour, matching its daily totals. The built-in Codex reader also groups timestamps in local time. Repeated hours during a daylight-saving fallback are combined. Older clients and snapshots may only have daily totals; these still count in the dashboard, and missing hourly coverage is shown in Notifications. Update and refresh/sync each device to add hourly detail.

## Pricing

Built-in rates cover common OpenAI/Codex, Anthropic/Claude, Google/Gemini and xAI/Grok models. `model_prices.json` includes provider source links and a verification date. The catalogue is an offline snapshot, not a live pricing feed. Check the provider's current rates before relying on an estimate.

Prices are USD per million tokens. Custom rates override matching models locally; `0` means free, and unused cache fields can stay blank. Existing nonzero collector estimates are preserved unless overridden. Missing costs are filled from the catalogue. Gemini aliases are normalized, cache tokens are charged once, and reasoning already included in output is not charged again.

Unknown models keep their tokens and appear in **Notifications**, with a shortcut to pricing settings. GPT-5.3-Codex-Spark remains unpriced because OpenAI has not published a final rate for its research preview. Daily aggregate fallbacks cannot infer request-level long-context premiums, media, paid tools, cache duration, service tiers, or subscription invoices. Synced costs retain the sender's estimate; another device's local price overrides do not rewrite them. Custom rate configuration itself stays local.

## Optional quota monitoring

If `quota-axi` is on your PATH, the app runs `quota-axi --provider codex --full --json` for quota display. If `codex` is available, a read-only app-server query can supply banked-reset information. These tools manage their own authentication. Missing quota tools do not block usage or sync. The app never consumes reset credits or uploads account/quota data.

## Local data and controls

Application data is stored under:

| System | Directory |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/splitrail-desktop` |
| Windows | `%LOCALAPPDATA%/splitrail-desktop` |
| macOS | `~/Library/Application Support/splitrail-desktop` |

`preferences.json` stores appearance and onboarding state; `model-prices.json` stores custom rates. `github-sync-v2.json` holds the repository and sync options, `sync-device.json` the random device identity, and `github-devices.json` downloaded aggregates. No credentials are stored by Splitrail. Files are written atomically with user-only permissions where the OS supports them.

The app streams `splitrail stats --include-messages` to calculate hourly activity from normalized timestamp/token/cost statistics. It retains only aggregate fields: session names, IDs, project metadata and unexpected content are discarded in memory, and the source records are never saved or synced. The collector’s daily totals remain authoritative; request-level cost precision can cause small rounding differences in hourly sums. Inconsistent hourly reasoning counts are omitted from the chart and sync’s optional hourly detail, while daily and model totals remain intact. Notifications explain any hourly coverage gap. The built-in reader scans `CODEX_HOME` (default `~/.codex`) and Codex-authenticated Pi logs in `~/.pi/agent/sessions`, extracting usage records only. It does not modify source logs. Codex totals count input plus output; reasoning is a subset of output.

Manual Codex transfer is available under **Settings → Usage & data → Export / Import**. Exports contain timestamps, model/source identifiers, token counts, hashed request/session identifiers, and scan metadata. They contain no conversation content. Manual imports merge by request identity and preserve older imported records. Manual export uploads nothing.

Normal usage refresh starts after launch and repeats every 15 minutes, switching to 5 minutes while usage changes. Codex quota and banked resets refresh separately every minute while the app is open, or retry after 5 minutes if a read fails. Automatic GitHub sync runs on usage refreshes only when explicitly enabled and viewing **All devices** or **Codex**. The header shows time since the last successful usage refresh; quota cards show the quota source’s refresh age. Stale quota values are marked as last known.

Executable discovery uses PATH and standard per-user install locations. Optional overrides: `SPLITRAIL_BIN`, `QUOTA_AXI_BIN`, `CODEX_BIN`.

## Command line

The commands below run from a source checkout. For the downloaded application, replace `splitrail-desktop` with `splitrail-desktop.pyz`; on Windows, replace `python3` with `py -3`.

```bash
python3 splitrail-desktop --codex-usage
python3 splitrail-desktop --export-usage usage.json.gz
python3 splitrail-desktop --import-usage usage.json.gz
python3 splitrail-desktop --setup-sync OWNER/REPO --sync-codex-only
python3 splitrail-desktop --sync-usage
```

CLI setup expects an existing private repository and GitHub CLI sign-in. Add `--receive-only` or `--auto-sync` to opt into those options. GUI setup can create the repository for you.

## Source layout

Core modules: `domain.py` aggregates usage, `pricing.py` resolves rates, `portable.py` handles Codex logs, `sync_payload.py` defines the wire format, `sync.py` handles private GitHub transport, and `desktop.py` bridges the engines to Qt, and `qml/` contains the dashboard, staged onboarding and reusable controls. `qt_app.py` loads resources from source, wheel or zipapp.
