# Splitrail Desktop GUI Reference

[Back to the quick start](../README.md) · [Privacy](../PRIVACY.md)

## What GitHub sync transfers

Each device gets a random identifier, independent of your hostname or account. It writes its own `usage/<random-id>.json` using the [GitHub Contents API](https://docs.github.com/en/rest/repos/contents). Only dates, hour buckets, known model/tool names, token counts, activity counts, and estimated USD costs are serialized. Unrecognized model/tool names are replaced with stable pseudonyms. No prompts, responses, paths, account/quota fields, credentials, diagnostic text, or raw session IDs are sent.

All devices can upload and download. Repeat syncs replace the same device snapshot; unchanged usage does not create another commit. The app uses the repository's default branch, verifies that it is private before transfers, and never creates workflows. Usage commits use a generic identity and `[skip ci]`. No personal Git author configuration is used by the sync transport.

Downloaded snapshots are validated before one atomic cache replacement. Offline or malformed responses preserve the last good totals. Removing a device file from the private repository removes its contribution after the next successful sync.

**Accounting boundary:** sync adds daily aggregates from distinct devices and preserves each sender's cost estimates. It cannot deduplicate copied session logs between devices because the all-tools collector does not provide request identities. Keep each device's source history distinct; do not also manually import the same usage that you sync. Manual Codex imports deduplicate against available local Codex/Pi request identities, independently of aggregate sync. Moving an entire app data directory to another computer also copies the device identity; remove `sync-device.json` and reconnect on the new computer before syncing.

The explicit **Codex** dashboard includes Codex rows from remote snapshots; **All devices** includes all received tools. A restored historical checkpoint preserves its daily/model/hourly totals and costs in the private `usage-history-v1.sqlite` app-data file. Both dashboards and outgoing sync use that same history. New Codex requests are added once by hashed identity and priced once; later log deletion, rescans and pricing changes cannot remove or reprice recorded requests. The request identities stay local. Other tools and received devices add positive aggregate increments after the checkpoint; a shrinking scan cannot lower saved usage. Aggregate-only sources cannot recover new usage hidden by simultaneous deletion of old records. Keep original logs and back up app data for complete coverage. The current sync limits are 100 device files and 8 MB per snapshot.

History recovery is explicit: select and review saved snapshots before initializing a checkpoint. It does not silently choose the largest historical estimate. The recorded cutoff and the source reference prevent adding the recovered totals twice. Counting or request-identity changes must bump `history.ACCOUNTING_VERSION`; the next refresh establishes a new reference while preserving the recorded totals. Recovery snapshots and the checkpoint database must never be committed to the source repository.

Hourly buckets use each source device’s local calendar date and clock hour, matching its daily totals. The built-in Codex reader also groups timestamps in local time. Repeated hours during a daylight-saving fallback are combined. Older clients and snapshots may only have daily totals; these still count in the dashboard, and missing hourly coverage is shown in Notifications. Update and refresh/sync each device to add hourly detail.

## Pricing

Built-in rates cover common OpenAI/Codex, Anthropic/Claude, Google/Gemini and xAI/Grok models. The public [`model_prices.json`](../src/splitrail_desktop/model_prices.json) is the shared catalogue, with provider source links and a verification date. During usage refresh the app checks this file on GitHub at most once per day, validates it and caches it locally. New models can receive rates without another app release. Failed or incompatible downloads keep the last valid rates; failures retry after an hour. Disable **Settings → Model pricing → Automatic price updates** to use saved/bundled rates without these requests. Demo mode never downloads prices.

GPT-6.1 Sol standard rates are $2 input, $0.10 cache reads, $2.50 cache writes and $10 output per million tokens. Requests exceeding 272,000 input tokens use 2× input/cache and 1.5× output rates when request-level usage is available. These are separate from GPT-6 Sol's $0.20 cache-read rate. [Official GPT-6.1 Sol rates](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Prices are USD per million tokens. Custom rates override matching models locally; `0` means free, and unused cache fields can stay blank. With a history checkpoint, recorded costs stay unchanged and new Codex requests use the current per-request rates. Without a checkpoint, Codex costs use the available request ledger. Other tools' existing nonzero collector estimates are preserved unless overridden. Missing costs are filled from the catalogue. Gemini aliases are normalized, cache tokens are charged once, and reasoning already included in output is not charged again.

Unknown models keep their tokens and appear in **Notifications**, with a shortcut to pricing settings. GPT-5.3-Codex-Spark remains unpriced because OpenAI has not published a final rate for its research preview. Daily aggregate fallbacks cannot infer request-level long-context premiums, media, paid tools, cache duration, service tiers, or subscription invoices. Synced costs retain the sender's estimate; another device's local price overrides do not rewrite them. Custom rate configuration itself stays local.

## Optional quota monitoring

If `quota-axi` is on your PATH, the app runs `quota-axi --provider codex --full --json` for quota display. If `codex` is available, a read-only app-server query can supply banked-reset information. These tools manage their own authentication. Missing quota tools do not block usage or sync. The app never consumes reset credits or uploads account/quota data.

## Codex process monitor and quota cutoff

The **Limits** page contains cutoff controls and accessible native `codex` / `codex.exe` engines owned by the current OS user. Rows show the folder, engine type and PID; hover over the row's **ⓘ** for its full paths, start time and OS status. Info icons also support keyboard focus. **Quota** remains a separate page for allowance and reset information. Wrapper processes, Codex helper hosts and Splitrail's own read-only quota subprocesses are excluded. WSL, containers, remote hosts and inaccessible processes may require Splitrail to run in the same environment as Codex. OS status describes the process, not chat activity; an app server can host multiple chats. Arbitrary shell commands and conversation text are not shown.

**Enable cutoff** authorizes termination of the selected engines while this instance of Splitrail is open. New engines are covered only if **Include new processes** is checked. With that option off, protection is bound to the selected PID and start time; a replacement process is not silently adopted. Before each stop, the app rechecks process ownership, executable and start time to avoid signalling a reused PID. Unix uses `SIGTERM`; Windows uses process termination. Splitrail does not force-kill a Unix engine that ignores the signal: it reports the failure and retries during subsequent process checks. It does not terminate arbitrary tool/shell descendants, which may continue independently.

Both limits are absolute percentages of the weekly allowance, from 0 to 100. The current limit applies immediately, including when usage is already at or above it. A newer fresh reading with lower usage, a different weekly window ID, a reset deadline changed by more than one minute, or a rollover past the previous deadline switches to the after-reset limit. This also covers observed unexpected resets with unchanged deadlines. The reset limit remains active through further resets until disarmed. Reset detection is based on observable quota changes: a reset followed by enough usage to hide the decrease between polls, with an unchanged deadline, cannot be identified reliably.

The guard needs fresh weekly data to arm. It rejects missing source timestamps, stale/failed reads and expired reset times. Duplicate or out-of-order readings never extend the freshness deadline or invent a reset. If no usable source reading has arrived for two minutes, it stops protected engines. Temporary failures keep retrying once per minute while armed. Once triggered, the cutoff remains blocked until explicitly disarmed, including across later quota resets. It never restarts terminated processes or redeems reset credits.

This is a **best-effort local guard**. Quota is polled once per minute after the preceding read completes; reporting latency, polling time, OS permissions, missed resets and requests already in flight can cause overshoot. Leave headroom below any strict target. It monitors the account reported by `quota-axi`, without attributing each process to an account. It cannot enforce an account-wide cap on cloud jobs or other devices. OpenAI documents rate-limit reads and interruptions within a connected app server in its [App Server reference](https://learn.chatgpt.com/docs/app-server); starting a separate server does not provide a universal control channel to already-open CLI or IDE conversations.

Process discovery runs in a background worker every ten seconds while the page is visible or protection is armed, with no overlapping scans. It reads names first and fetches detailed metadata only for matching candidates. It does not scan usage histories, sample CPU continuously, or add network polling beyond the normal quota refresh. Percentages persist in `preferences.json`; process selections, paths and armed state stay in memory. Quitting the application cancels pending stops and disables the guard; closing the macOS window leaves the guard running in the menu bar. Start it again and explicitly arm to protect another run. Demo mode uses synthetic process rows and cannot terminate processes.

## Local data and controls

Application data is stored under:

| System | Directory |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/splitrail-desktop` |
| Windows | `%LOCALAPPDATA%/splitrail-desktop` |
| macOS | `~/Library/Application Support/splitrail-desktop` |

`preferences.json` stores appearance, onboarding state, automatic price-update preference and quota cutoff percentages; `model-prices.json` stores custom rates and `price-catalog.json` caches validated public prices and their last successful check time. `github-sync-v2.json` holds the repository and sync options, `sync-device.json` the random device identity, and `github-devices.json` downloaded aggregates. No credentials are stored by Splitrail. Files are written atomically with user-only permissions where the OS supports them.

The app streams `splitrail stats --include-messages` to calculate hourly activity from normalized timestamp/token/cost statistics. It retains only aggregate fields: session names, IDs, project metadata and unexpected content are discarded in memory, and the source records are never saved or synced. Codex token counts, costs and hourly bins use the built-in request ledger, combined with any preserved historical checkpoint, in every local view and All tools uploads. The ledger handles newer request records, repeated UI counters, archived sessions and inherited fork baselines; collector message/tool metadata is retained. Other tools keep their collector totals or preserved checkpoint and subsequent increments. If Codex logs cannot be read or contain invalid usage, saved history or collector totals are retained with a notification. Pi remains a single collector source in All tools, avoiding overlap with Codex-authenticated Pi requests.

Request-level cost precision can cause small rounding differences in hourly sums. Inconsistent collector hourly reasoning counts are omitted from the chart and sync’s optional hourly detail, while daily and model totals remain intact. Notifications explain any hourly coverage gap. The built-in reader scans `CODEX_HOME` (default `~/.codex`) and, in the Codex view, Codex-authenticated Pi logs in `~/.pi/agent/sessions`, extracting usage records only. It does not modify source logs. New Codex totals count input plus output; reasoning is a subset of output. All time includes preserved historical figures, subsequent recorded usage, available local records and downloaded snapshots as applicable. A preserved historical figure retains the original accounting method; recovery does not certify that older estimate as a count of distinct requests.

Manual Codex transfer is available under **Settings → Usage & data → Export / Import**. Exports contain timestamps, model/source identifiers, token counts, hashed request/session identifiers, and scan metadata. They contain no conversation content. Manual imports merge by request identity and preserve older imported records. Manual export uploads nothing.

Token and cost totals refresh after launch, then hourly while unchanged or every 5 minutes while activity is detected. A lightweight once-per-minute check compares Codex/Pi log file sizes and modification times without reading their contents; changes, or a changed quota percentage, bring the next totals refresh forward to 5 minutes after the last successful refresh (immediately if already overdue). An unchanged totals refresh returns to the hourly schedule. Manual Refresh remains available. Codex quota and banked resets refresh separately every minute, including retries after failed reads. The header shows time since the last successful usage refresh; quota cards show the quota source’s refresh age. Stale quota values are marked as last known.

History-heavy refresh, sync and transfer jobs run in temporary helper processes that exit after returning their results, releasing their working memory. The parent blocks while waiting for completion instead of polling the helper every 50 ms. Quitting the app also stops its helpers and collectors. For longer date ranges, the external collector returns daily aggregates without per-message detail. Opening Today or another single-day range requests hourly detail; it is reused until the next totals refresh. Deferred hourly detail preserves saved daily totals and catches up without counting usage twice. Sync continues to include available hourly data.

On macOS, the native bundle uses Apple's `LSUIElement` mode and Qt's native menu-bar integration. Closing the window hides it; **Quit Splitrail** in its menu stops the application. Hidden/minimized windows unload QML dashboard delegates and release their graphics resources. They pause automatic usage collection, metadata activity scans, countdown updates and unarmed process scans. Quota checks use five minutes while hidden and unarmed, and one minute while visible or armed. An armed cutoff retains the one-second stale-data watchdog and ten-second process scan. Automatic private sync retains its independent schedule; refreshed dashboard totals are loaded when the window returns. Long-interval Qt timers allow the OS to coalesce wakeups. See [Apple's timer guidance](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/Timers.html) and [Qt's system tray API](https://doc.qt.io/qt-6/qsystemtrayicon.html).

Automatic GitHub sync has its own schedule in every dashboard view, including **This device**, and requires explicit opt-in. The first sync runs when enabled and no prior attempt exists; subsequent syncs wait **1 hour**, or **3 hours** if neither local nor downloaded totals changed. A detected change returns to the shorter interval. Under **Settings → GitHub sync → Auto sync**, edit **Every** and **If unchanged**, then **Save**. Values are whole hours from 1 to 168, with the unchanged interval at least as long as the normal interval. Timing settings can be saved offline. The last attempt and result persist across restarts; failed attempts retry after the normal interval while keeping saved totals. **Sync now** bypasses the wait and resets the schedule. Keep the app open for automatic sync.

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

Core modules: `domain.py` aggregates usage, `pricing.py` resolves rates, `portable.py` handles Codex logs, `codex_totals.py` reconciles Codex counts across views, `sync_payload.py` defines the wire format, `sync.py` handles private GitHub transport, `quota_guard.py` evaluates cutoff/reset decisions, and `processes.py` discovers and stops verified local engines. `desktop.py` bridges the engines to Qt, and `qml/` contains the dashboard, staged onboarding and reusable controls. `qt_app.py` loads resources from source, wheel or zipapp.
