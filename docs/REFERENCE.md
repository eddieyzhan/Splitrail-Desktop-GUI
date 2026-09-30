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

Built-in rates cover common OpenAI/Codex, Anthropic/Claude, Google/Gemini and xAI/Grok models. The public [`model_prices.json`](../src/splitrail_desktop/model_prices.json) is the shared catalogue, with provider source links and a verification date. During usage refresh the app checks this file on GitHub at most once per day, validates it and caches it locally. New models can receive rates without another app release. Failed or incompatible downloads keep the last valid rates; failures retry after an hour. Disable **Settings → Model pricing → Automatic price updates** to use saved/bundled rates without these requests. Demo mode never downloads prices.

GPT-6.1 Sol standard rates are $2 input, $0.10 cache reads, $2.50 cache writes and $10 output per million tokens. Requests exceeding 272,000 input tokens use 2× input/cache and 1.5× output rates when request-level usage is available. These are separate from GPT-6 Sol's $0.20 cache-read rate. [Official GPT-6.1 Sol rates](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Prices are USD per million tokens. Custom rates override matching models locally; `0` means free, and unused cache fields can stay blank. Existing nonzero collector estimates are preserved unless overridden. Missing costs are filled from the catalogue. Gemini aliases are normalized, cache tokens are charged once, and reasoning already included in output is not charged again.

Unknown models keep their tokens and appear in **Notifications**, with a shortcut to pricing settings. GPT-5.3-Codex-Spark remains unpriced because OpenAI has not published a final rate for its research preview. Daily aggregate fallbacks cannot infer request-level long-context premiums, media, paid tools, cache duration, service tiers, or subscription invoices. Synced costs retain the sender's estimate; another device's local price overrides do not rewrite them. Custom rate configuration itself stays local.

## Optional quota monitoring

If `quota-axi` is on your PATH, the app runs `quota-axi --provider codex --full --json` for quota display. If `codex` is available, a read-only app-server query can supply banked-reset information. These tools manage their own authentication. Missing quota tools do not block usage or sync. The app never consumes reset credits or uploads account/quota data.

## Codex process monitor and quota cutoff

The **Limits** page contains cutoff controls and accessible native `codex` / `codex.exe` engines owned by the current OS user. Rows show the folder, engine type and PID; hover over the row's **ⓘ** for its full paths, start time and OS status. Info icons also support keyboard focus. **Quota** remains a separate page for allowance and reset information. Wrapper processes, Codex helper hosts and Splitrail's own read-only quota subprocesses are excluded. WSL, containers, remote hosts and inaccessible processes may require Splitrail to run in the same environment as Codex. OS status describes the process, not chat activity; an app server can host multiple chats. Arbitrary shell commands and conversation text are not shown.

**Enable cutoff** authorizes termination of the selected engines while this instance of Splitrail is open. New engines are covered only if **Include new processes** is checked. With that option off, protection is bound to the selected PID and start time; a replacement process is not silently adopted. Before each stop, the app rechecks process ownership, executable and start time to avoid signalling a reused PID. Unix uses `SIGTERM`; Windows uses process termination. Splitrail does not force-kill a Unix engine that ignores the signal: it reports the failure and retries during subsequent process checks. It does not terminate arbitrary tool/shell descendants, which may continue independently.

Both limits are absolute percentages of the weekly allowance, from 0 to 100. The current limit applies immediately, including when usage is already at or above it. A newer fresh reading with lower usage, a different weekly window ID, a reset deadline changed by more than one minute, or a rollover past the previous deadline switches to the after-reset limit. This also covers observed unexpected resets with unchanged deadlines. The reset limit remains active through further resets until disarmed. Reset detection is based on observable quota changes: a reset followed by enough usage to hide the decrease between polls, with an unchanged deadline, cannot be identified reliably.

The guard needs fresh weekly data to arm. It rejects missing source timestamps, stale/failed reads and expired reset times. Duplicate or out-of-order readings never extend the freshness deadline or invent a reset. If no usable source reading has arrived for two minutes, it stops protected engines. Temporary failures keep retrying once per minute while armed. Once triggered, the cutoff remains blocked until explicitly disarmed, including across later quota resets. It never restarts terminated processes or redeems reset credits.

This is a **best-effort local guard**. Quota is polled once per minute after the preceding read completes; reporting latency, polling time, OS permissions, missed resets and requests already in flight can cause overshoot. Leave headroom below any strict target. It monitors the account reported by `quota-axi`, without attributing each process to an account. It cannot enforce an account-wide cap on cloud jobs or other devices. OpenAI documents rate-limit reads and interruptions within a connected app server in its [App Server reference](https://learn.chatgpt.com/docs/app-server); starting a separate server does not provide a universal control channel to already-open CLI or IDE conversations.

Process discovery runs in a background worker every ten seconds while the page is visible or protection is armed, with no overlapping scans. It reads names first and fetches detailed metadata only for matching candidates. It does not scan usage histories, sample CPU continuously, or add network polling beyond the normal quota refresh. Percentages persist in `preferences.json`; process selections, paths and armed state stay in memory. Closing the application cancels pending stops and disables the guard. Start it again and explicitly arm to protect another run. Demo mode uses synthetic process rows and cannot terminate processes.

## Local data and controls

Application data is stored under:

| System | Directory |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/splitrail-desktop` |
| Windows | `%LOCALAPPDATA%/splitrail-desktop` |
| macOS | `~/Library/Application Support/splitrail-desktop` |

`preferences.json` stores appearance, onboarding state, automatic price-update preference and quota cutoff percentages; `model-prices.json` stores custom rates and `price-catalog.json` caches validated public prices and their last successful check time. `github-sync-v2.json` holds the repository and sync options, `sync-device.json` the random device identity, and `github-devices.json` downloaded aggregates. No credentials are stored by Splitrail. Files are written atomically with user-only permissions where the OS supports them.

The app streams `splitrail stats --include-messages` to calculate hourly activity from normalized timestamp/token/cost statistics. It retains only aggregate fields: session names, IDs, project metadata and unexpected content are discarded in memory, and the source records are never saved or synced. The collector’s daily totals remain authoritative; request-level cost precision can cause small rounding differences in hourly sums. Inconsistent hourly reasoning counts are omitted from the chart and sync’s optional hourly detail, while daily and model totals remain intact. Notifications explain any hourly coverage gap. The built-in reader scans `CODEX_HOME` (default `~/.codex`) and Codex-authenticated Pi logs in `~/.pi/agent/sessions`, extracting usage records only. It does not modify source logs. Codex totals count input plus output; reasoning is a subset of output.

Manual Codex transfer is available under **Settings → Usage & data → Export / Import**. Exports contain timestamps, model/source identifiers, token counts, hashed request/session identifiers, and scan metadata. They contain no conversation content. Manual imports merge by request identity and preserve older imported records. Manual export uploads nothing.

Normal usage refresh starts after launch and repeats every 15 minutes, switching to 5 minutes while usage changes. Codex quota and banked resets refresh separately every minute while the app is open, or retry after 5 minutes if a read fails. Automatic GitHub sync runs on usage refreshes only when explicitly enabled and viewing **All devices** or **Codex**. The header shows time since the last successful usage refresh; quota cards show the quota source’s refresh age. Stale quota values are marked as last known.

Executable discovery uses PATH and standard per-user install locations. Optional overrides: `SPLITRAIL_BIN`, `QUOTA_AXI_BIN`, `CODEX_BIN`.

## Command line

The commands below run from a source checkout. For the downloaded application, replace `splitrail-desktop` with `splitrail-desktop.pyz`; on Windows, replace `python3` with `py -3`.

```bash
python3 splitrail-desktop --codex-usage
python3 splitrail-desktop --quota
python3 splitrail-desktop --limits
python3 splitrail-desktop --export-usage usage.json.gz
python3 splitrail-desktop --import-usage usage.json.gz
python3 splitrail-desktop --setup-sync OWNER/REPO --sync-codex-only
python3 splitrail-desktop --sync-usage
```

CLI setup expects an existing private repository and GitHub CLI sign-in. Add `--receive-only` or `--auto-sync` to opt into those options. GUI setup can create the repository for you.

## Source layout

Core modules: `domain.py` aggregates usage, `pricing.py` resolves rates, `portable.py` handles Codex logs, `sync_payload.py` defines the wire format, `sync.py` handles private GitHub transport, `quota_guard.py` evaluates cutoff/reset decisions, and `processes.py` discovers and stops verified local engines. `desktop.py` bridges the engines to Qt, and `qml/` contains the dashboard, staged onboarding and reusable controls. `qt_app.py` loads resources from source, wheel or zipapp.
