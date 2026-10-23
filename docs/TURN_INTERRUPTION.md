# Native macOS turn interruption

`macos/TurnStopper.swift` requests an exposed generation Stop action through the public macOS Accessibility API. It does not terminate Codex, close ChatGPT, delete conversations, inject JavaScript, send keyboard events, start an app-server, or alter permissions. The native app can compile it directly with AppKit and ApplicationServices; no Python or Qt runtime is needed for this component.

## Research and verified scope

OpenAI Docs research was performed on 2026-10-23. The official [app-server documentation](https://learn.chatgpt.com/docs/app-server) defines `turn/interrupt` using `threadId` and `turnId`. Its empty response acknowledges the request; the later completed-turn notification carries the interrupted status. Connections require initialization. The documented transports include stdio, WebSocket, and Unix sockets; the WebSocket transport is described as experimental and unsupported for production.

The [developer command reference](https://learn.chatgpt.com/docs/developer-commands) describes selecting a server listener and connecting the CLI to a specified/default Unix socket. Neither fetched page establishes a supported way for this separate app to discover and authenticate to the *already running desktop app's* server and bind its visible Stop button to a particular active turn. That missing attachment contract is why this implementation uses AX. This is a limitation of the evidence, **not proof that desktop RPC attachment is impossible**. No live socket was probed, and no credentials, process arguments, private databases, or conversation logs were inspected. A future RPC integration should use an explicitly supplied existing connection and authoritative thread/turn IDs, rather than infer them from persisted history or launch another engine.

Read-only CUA application inventory identified the running app as **ChatGPT, `com.openai.codex`**. That is the sole bundle allowlist entry. `com.openai.chat` was not found and is deliberately not assumed to be interchangeable.

CUA refused inspection of `com.openai.codex` for safety reasons. Inspection was not retried through another mechanism. Consequently the current installed app's Stop label, composer structure, and live AXPress behavior **have not been verified**. The exact English labels below are conservative supported patterns, not a claim about this installed version. A version exposing only an ambiguous bare `Stop`, an unsupported structure, or localized labels will fail closed. No actual cutoff was exercised during development.

## Controller interface

```swift
let stopper = TurnStopper()             // No inspection, permission prompt, or action.
let capability = stopper.capability()   // Read-only, synchronous.
let outcome = stopper.interrupt()       // Fresh scan and at most one AXPress.
```

Both methods serialize access within the instance. Run them on a serial background queue and update AppKit UI on the main queue. AX calls can block: each IPC call has a 150 ms element-local timeout, each scan has a two-second traversal budget, and interruption performs two scans plus final validation under a five-second budget. These are best-effort deadlines checked between IPC calls, not a hard real-time guarantee. The library has no resident worker, timers, observers, subprocesses, or automatic retries.

`Capability` and `Outcome` are `Equatable` and `Sendable` and expose a sanitized `message`. `Capability.canInterrupt` is true only for `.ready`.

| Capability | Meaning |
| --- | --- |
| `.ready` | Exactly one eligible, currently exposed generation Stop control was found. |
| `.permissionRequired` | The process running the library lacks Accessibility trust. |
| `.appNotRunning` | No running application has the supported bundle identifier. |
| `.noActiveTurn` | No supported generation Stop control was exposed in the inspected UI. This does **not** establish that all turns are idle. |
| `.unavailable(reason)` | Inspection could not establish a safe unique target. |

`Reason` is `.noVisibleWindow`, `.ambiguousControls`, `.unverifiedStopControl`, `.unsupportedControl`, `.accessibilityUnavailable`, or `.inspectionLimitReached`.

`Outcome` has the same non-ready cases plus:

| Outcome | Meaning |
| --- | --- |
| `.stopRequested` | AXPress returned success. Display **Stop requested**, not **Turn cancelled**. No server confirmation was received. |
| `.targetChanged` | The target, application, window, or control evidence changed before dispatch. No action was sent. |
| `.failed` | AXPress was attempted once and was not acknowledged. Delivery is uncertain; do not retry automatically. |

The parent permits arming only for `.ready`. An idle UI cannot prove that a future generation will expose a compatible control, so `.noActiveTurn` does not permit arming. Other results are shown as unavailable, not silently treated as enforcement success. Permission prompting belongs in the parent's deliberate `requestStopPermission()` UI action, using the public `AXIsProcessTrustedWithOptions` prompt option. Neither library method prompts or opens System Settings. `performStop()` maps the exact `Outcome` to UI state.

## Targeting and privacy

The scanner requires all of the following before dispatch:

1. The allowlisted application has a positive PID and a known launch date; both are checked again to guard against PID reuse. The app stays in place: no activation, unhide, window raising, or focus change is requested.
2. The target belongs to an accessible, non-minimized, on-screen window of that process. The button and composer input must be inside that window and intersect an active screen. This checks accessibility geometry, not pixel-level occlusion or every macOS Space.
3. The control is an enabled `AXButton` offering `AXPress`, with exactly `Stop generating` or `Stop generation` in its title, description, or help. Outer whitespace is trimmed; case and the remaining text must match exactly. Other nonempty labels must be those same generation labels, `Stop`, or the neutral `button`. Conflicting labels fail closed. A bare `Stop` has insufficient meaning.
4. The button belongs to a small composer group with exactly one visible, editable multiline `AXTextArea` owned by the same app/window. Writability is queried as attribute metadata; text is never read or set. The search examines at most three enclosing groups, four levels and 32 nodes per group. It cannot cross a list, scroll area, WebArea, or dialog to associate a sidebar/transcript button with a distant composer. A conversation titled `Stop generating` therefore does not qualify solely because of its title.
5. Menus, dialog windows, text content, and nested browser/tool WebAreas are excluded. The total traversal is bounded by 2,000 nodes, 64 levels and 16 windows. A partial or failed inspection cannot produce a ready result. Multiple candidates are refused, including candidates in different windows.
6. Two complete scans agree on the app identity, window, composer, button, and control evidence. The selected target is checked once more immediately before the single AXPress. There is no fallback to Escape, click coordinates, signals, force-quit, hidden RPC, or app restart.

AX does not supply a transactional thread/turn identity. A turn may finish, a new turn may reuse the same UI element, or the UI may change in the final interval before AXPress. These checks reduce that race but cannot eliminate it. The same app may expose other conversation modes; this library cannot prove a backend/model identity from AX alone. The guarded scope must be described as the eligible visible ChatGPT/Codex composer, not every Codex engine.

Only button control labels are inspected transiently. The native adapter retains fixed recognized labels or an unrecognized marker; arbitrary button titles/help are discarded. It never reads editor values, transcript text, window titles, tokens, logs, or session files. Public outcomes contain no process IDs, AX dumps, account names, conversation text or raw platform errors. There is no logging or telemetry in the library.

## One attempt per cutoff

The parent controller owns the armed/blocked policy and intentionally requests Stop **once per cutoff**, with no observer, automatic retry, or repeated enforcement while blocked. This prevents a retry from unintentionally acting on a different turn after the original control changes or delivery is uncertain. The library does not add a background worker or alter that policy. The parent performs the capability check off the main thread before arming and owns the explicit permission-prompt button.

A user can start another generation while the guard remains blocked; this one-shot cutoff does not automatically interrupt it. A fresh explicit arming/cutoff cycle is required for another request. UI and documentation must not imply that the current implementation prevents new generation or continually stops it. Continuous enforcement would be a separate scope decision; authoritative thread/turn IDs would be preferable to inferring new turns from AX element disappearance/reappearance. Neither a one-shot AX action nor AX notifications establish a strict admission barrier or exact spending guarantee.

Hidden/minimized apps, background or nonselected threads, remote/cloud turns, terminal/IDE sessions, and inaccessible controls may continue running. This library neither enumerates those turns nor prevents new ones from starting. The UI should state that limitation and keep the chat/app open. No cancellation path should ever fall back to terminating the engine.

## Mock-only verification

The standalone test executable injects `FakeAX` into every `TurnStopper`. It never constructs the real backend, queries system AX, asks for permissions, starts an engine, or presses a live Stop button. The tests include actual executions of harmless no-op paths for permission denial, no application, no active control, unsupported controls, ambiguous controls, stale targets, visibility/ownership failures, IPC failure and traversal limits. Successful and failed AXPress cases only append an integer to a fake array.

Compile with the existing CLT compiler and an explicit compatible SDK; do not install tools or change `xcode-select` or license settings:

```sh
/Library/Developer/CommandLineTools/usr/bin/swiftc \
  -swift-version 6 -warnings-as-errors -D TURN_STOPPER_TESTS \
  -sdk /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk \
  -target arm64-apple-macosx13.0 \
  -module-cache-path build/macos-native/test-cache \
  macos/TurnStopper.swift macos/tests/TurnStopperTests.swift \
  -o build/macos-native/TurnStopperTests
build/macos-native/TurnStopperTests
```

The 35 mock-only tests pass with Swift 6 and warnings treated as errors. Live inspection and a sacrificial real-turn interruption remain unverified and were not performed to avoid interrupting the user’s ongoing work. Do not wire a real interruption into build checks, application self-checks, installation, or launch.
