# Native Mac app

Version 1.3.0 replaces the resident Python/Qt Mac application with Swift and AppKit. Existing accounting, pricing and private-sync code runs in short-lived headless workers, retaining tested cross-device compatibility. No worker is retained when its job finishes, and no Qt modules or libraries are included in this bundle.

Version 1.3.1 is a development preview adding an unavailable Auto-recover switch and a synthetic **Test logic** action under Limits. Live recovery is not implemented. It installs no recovery observers, polling, timers or helpers; the test runs only when requested. Limits grows to 450 × 480 to fit the status and test control. The overview is unchanged. See [AUTO_RECOVERY.md](AUTO_RECOVERY.md) for implemented policy, required integration and the live testing block.

The 450 × 370 dashboard shows plain totals, quota percentages, reset dates and model costs. Astra/Sol/Luna names group matching model-family costs; hover for the exact model IDs. There is one usage timestamp, regular-weight text, no reset count, no redundant arrows and no graph. The sidebar contains dashboard, Limits and Settings. Date selection, theme, custom prices, transfer and private sync remain available.

## Measurements

Measured on an Apple silicon Mac running macOS 26.6 on 2026-10-23, using real local usage and the installed release. Values are macOS physical footprint in MiB, sampled every 100 ms. The previous 1.2.6 Qt app was measured on the same machine. These are observations, not fixed limits.

| Scenario | Resident app | Peak including disposable helpers | Sample |
| --- | ---: | ---: | ---: |
| Previous Qt app, idle visible | 105.0 | 105.0 | 30 s |
| Native dashboard, idle after launch | 28.2 | 28.2 | 30 s |
| Native usage + routine quota refresh | 28.0 | 47.5 | 25 s |
| Native private GitHub sync | 27.8 | 67.4 | 30 s |
| Native Settings + initial reset-date query | 37.3 | 159.9 | 25 s |
| Native hidden after using Settings | 35.3 | 35.3 | 30 s |

Opening native controls can retain system/framework caches after closing the window, so the app does not promise to return to its initial 28 MiB footprint. The initial/periodic reset-date query still starts a read-only Codex helper: its roughly 160 MiB total peak remains a target for future work. Routine quota reads used only one small Python worker. Sync included the GitHub CLI and its credential helper; no helpers remained at idle.

Visible idle sampled 0.000% CPU with one package-idle wakeup over 30 seconds; hidden idle after interaction sampled 0.003% with three. The old Qt sample was 0.168% with 303 package-idle wakeups over 30 seconds. This is a short workload comparison, not a battery-life estimate. Refresh and sync naturally consume CPU and network activity while running. An armed cutoff deliberately needs more frequent checks and was not included in these live idle measurements.

Validation completed 352 Python tests (3 skipped), native model tests, 35 mock-only Stop tests, bundle self-checks and the synthetic external-collector test. The installed app displayed actual usage, quota and reset dates and successfully synced three devices. A live generation Stop was not tested; see the limits below.

## Scheduling and energy

- FSEvents batches local log-change notifications. A visible app collects at most once per minute during ongoing changes; hidden collection is batched to five minutes. It never watches the entire home directory for an absent tool.
- A discretionary four-hour reconciliation catches missed events and sources beyond the watched Codex/Pi/Claude directories. Closing the panel releases its views. There is no display clock, animation or process-inventory polling.
- Unarmed quota is read on opening when outdated, then every five minutes while visible. Hidden unarmed quota polling stops completely. Routine reads use the existing provider's HTTPS usage endpoint in the small worker. Codex RPC refreshes authoritative reset dates on first use, hourly, or when reset/credit state changes; their observation time remains available in hover help. Unsupported HTTP/auth responses fall back to RPC, with the Node quota tool only an older-CLI fallback. Normal jobs are serialized to reduce peak memory; an armed quota read can take priority.
- An armed cutoff polls quota every 45 seconds after a read completes and owns a separate two-minute freshness deadline. It temporarily prevents App Nap while allowing system sleep. Sleep suspends timers; wake checks stale protection before requesting fresh data. This mode deliberately consumes more energy than passive tracking.
- Automatic GitHub sync preserves opt-in, privacy checks, configured normal/unchanged intervals and persisted last-attempt timing. Manual Sync bypasses the delay. Offline attempts back off instead of spinning.
- The persistent usage scanner caches content-free parser state and usage records. Appended logs are processed incrementally; replacements, truncation and archive movement are reconciled. No prompts, responses or tool output enter the cache or sync payload.

Apple recommends event notifications instead of polling and tolerance for timers that remain necessary. The design uses [FSEvents](https://developer.apple.com/library/archive/documentation/Darwin/Conceptual/FSEvents_ProgGuide/UsingtheFSEventsFramework/UsingtheFSEventsFramework.html), [timer coalescing](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/Timers.html) and AppKit's [NSStatusItem](https://developer.apple.com/documentation/appkit/nsstatusitem).

## Why not a desktop widget or webview?

A WidgetKit widget is a useful passive display, but its extension is not continuously active and macOS controls refresh budgets. Apple describes typical frequently viewed widget budgets as 40–70 refreshes per day, with variable timing. It cannot own this app's timely quota watchdog. Adding a widget also does not remove the need for a controller and accounting worker. The native menu-bar app provides an optional Keep on screen panel without another extension or runtime. See [Apple's widget refresh guidance](https://developer.apple.com/documentation/widgetkit/keeping-a-widget-up-to-date/).

Tauri would remove bundled Qt, but it introduces a system webview with its own process model, so it does not establish a lower resident memory floor. Pure AppKit avoids that layer. A Rust accounting core could reduce transient parsing work further, but does not need to precede the substantial resident-process savings delivered here. See [Tauri's process model](https://v2.tauri.app/concept/process-model/).

## Limits

The Mac cutoff requests generation Stop, never SIGTERM/SIGKILL on a Codex engine. Accessibility permission and a verified exposed generation control are required before arming. It cannot protect hidden turns, terminals, cloud tasks or other computers. The cutoff makes one request, shows the result and stays latched until disarmed; it does not automatically stop newly started turns. AXPress acceptance is not a server acknowledgement. Full details and conservative matching rules are in [TURN_INTERRUPTION.md](TURN_INTERRUPTION.md).

## Building and testing

Use an existing Apple Swift toolchain and SDK; the build script never installs or changes either. Set `DEVELOPER_DIR` and `SDKROOT` if the selected SDK does not match the installed compiler.

```sh
python -m pip install -r scripts/requirements-release.txt
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/build.py
python scripts/build_native.py
```

On Mac, `build_native.py` dispatches to `build_macos.py`, builds the worker without Qt, compiles optimized Swift, signs locally, checks the application/worker and exercises a synthetic external collector. Native model and turn-control tests live under `macos/tests`. No GitHub Actions or Codemagic execution is needed.

For physical footprint, sampled CPU and wakeups including live worker descendants:

```sh
python scripts/profile_native_macos.py APP_PID --seconds 30
```

Measure idle visible, idle hidden, collection, quota refresh and GitHub sync separately. The report separates resident-app memory from the observed process-tree peak and names helper executables only. Sampling can miss a process shorter than 100 ms; it is not a battery-discharge benchmark. macOS physical footprint is used instead of RSS. `scripts/profile_macos.py` remains available for synthetic comparisons with the older Qt application.
