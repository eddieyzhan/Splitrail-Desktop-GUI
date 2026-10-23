# Auto-recover preview for native Mac

**Live Auto-recover is not implemented or enabled in this build.** Version 1.3.1 contains the recovery policy, synthetic tests and a compact **Limits → Test logic** control. The checkbox says **Auto-recover · unavailable** and cannot be enabled. The installed Codex application's capacity, Retry, Fast, Stop and approval controls have not been inspected or verified.

During development, the computer-use tool explicitly refused access to `com.openai.codex` for safety reasons. The refusal was not worked around with another accessibility client, private socket, configuration edits or a second engine. It blocks live integration discovery and end-to-end testing. Passing the tests below does **not** mean the feature can recover a real conversation. Existing usage tracking, private GitHub sync and the separate one-shot quota cutoff remain unchanged.

## Implemented policy

`macos/AutoRecovery.swift` is a pure Swift state machine. It accepts structured observations and returns a proposed command; it cannot execute commands or read an application. An episode is scoped to an explicitly enabled application instance, conversation and turn. No conversation text, editor contents or credentials are part of this model.

- Only an explicitly classified **capacity** failure is eligible. Quota, authentication, network and other failures do not trigger retries.
- Wait 5, 15, 45 and 90 seconds between attempts, respecting a longer server retry deadline. A later server delay can extend the wait, never shorten it. Invalid advertised delays fail closed.
- If Fast is already enabled, request Normal and verify the change before retrying. Otherwise try Normal once before requesting a temporary Fast burst. No blind toggle is used.
- Prefer a targeted Retry. A targeted `continue` is allowed once per episode when Retry is unavailable. If neither is safe, stop recovery before changing mode.
- At most four generation attempts and one Fast burst per episode. Across explicit re-enables in the same process, permit at most two Fast bursts per rolling hour, at least 15 minutes apart. A restart starts disabled; the future live adapter must decide how to persist burst budgets safely.
- Start the 120-second Fast deadline **before** dispatching the enable command. Errors, retries and acknowledgements cannot extend it. If the recovered turn finishes sooner, request Normal immediately.
- At the deadline, a running turn is stopped first. Only after causally confirmed interruption, confirmed Normal mode and fresh override checks may one continuation start. A failed turn waiting on a longer Retry-After is returned to Normal without retrying early. These are proposed actions, not a guarantee of an exact provider-side usage cutoff.
- Manual Stop, an armed usage cutoff, stale quota, user edits, drafts, approvals and conversation changes override recovery. Overrides latch; clearing a prompt or draft does not silently restart generation. A safe Normal-only restoration can still be requested for a temporary Fast change owned by this episode. Drafts and approvals block even that mutation and leave explicit restoration debt.
- Re-read the exact target, revision, mode and guards immediately before dispatch. A changed target, stale/out-of-order state or unknown mode fails closed. A new turn ID can be adopted only from a causally correlated retry/continue receipt for the same conversation.
- An accepted click or RPC request is **not** a successful mode change or stopped turn. Every action needs authoritative confirmation within five seconds. Uncertain delivery never triggers an automatic repeat. Failed restoration stays unresolved instead of reporting success.

Disabling recovery cancels further generation. An unresolved Fast restoration must not be discarded by re-enabling. No code path closes a conversation, terminates an engine, edits a draft, submits an approval, changes the model, redeems reset credits or changes global Codex settings.

## Required before enabling a live adapter

The production `DesktopRecoveryAvailability.canEnable` constant is false. `AppController` only invokes the synthetic diagnostic. There is no live recovery observer, scheduler, integration adapter, or persisted enabled preference. Do not turn the flag on to make the checkbox look functional.

Live implementation still needs a supported integration that can establish all of the following:

1. Authoritative application/thread/turn identity, normalized capacity failures, server retry delays, user Stop, drafts, approvals and current mode. A visible error sentence alone cannot supply this contract. Tool output and conversation content must never be treated as control instructions.
2. Event delivery with revisions, user-interaction changes and causal operation receipts. A vanished Stop button or successful AXPress does not prove that a specific turn stopped.
3. A final atomic precondition check before each command, cancellation of queued commands, serialization of mode changes, and an override path shared with the quota guard. No operation may apply after a user Stop or a cutoff has invalidated it. The policy alone cannot cancel a side effect already dispatched by an external adapter.
4. A sleep-aware continuous clock, one-shot timers for the earliest deadline, event coalescing and no continuous screen scanning. Revalidate state on wake. A timer firing at 120 seconds does not prove a provider stopped charging Fast at that instant.
5. Crash/quit handling and a durable, private record of any temporary Fast change that still requires Normal restoration. The current synthetic policy keeps its state in memory; it does not solve process-crash restoration. Preserve manual mode choices instead of overwriting them on restart.
6. Live tests in a disposable, explicitly selected conversation: actual error detection, Retry/Continue targeting, mode changes, Stop precedence, drafts, approvals, delayed/missing acknowledgements, sleep/wake and an active-turn transition back to Normal. A real capacity incident is needed to validate the full path; simulations cannot force an actual service capacity failure.

The existing `TurnStopper` has a narrower one-shot visible-composer contract and cannot provide these guarantees. Auto-recover does not reuse it to infer a conversation identity. See [TURN_INTERRUPTION.md](TURN_INTERRUPTION.md) for its independent limitations.

## Tests and UI

The same synthetic fixture drives **Limits → Test logic**, `--test-recovery`, the bundle self-check and the standalone suite. It mutates an in-memory model only and cannot reach Codex, the network, local logs, approvals or credentials. The in-app result explicitly says **Live Codex untested**.

```sh
Splitrail.app/Contents/MacOS/Splitrail --test-recovery
```

The in-app check contains 12 scenarios. The standalone suite adds deadline, confirmation, override-race, identity, cooldown, quota/error classification, duplicate notification and invalid-time cases, plus 10,000 idle notifications without an action. Compile it with an existing Apple toolchain:

```sh
swiftc -O -swift-version 6 -warnings-as-errors \
  macos/AutoRecovery.swift macos/RecoverySimulation.swift \
  macos/tests/AutoRecoveryTests.swift -o /tmp/splitrail-recovery-tests
/tmp/splitrail-recovery-tests
```

`scripts/build_macos.py` runs this suite with its explicitly configured toolchain and packages this document. No GitHub Actions or Codemagic runs are necessary. A successful preview test does not change permissions or enable live recovery.

## Research

Official documentation checked on 2026-10-23:

- [Speed](https://learn.chatgpt.com/docs/agent-configuration/speed): Fast consumes included subscription limits at 2.5× the Standard rate; purchased credits/Enterprise pay-as-you-go use 2×. It does not promise that changing tiers resolves a capacity error.
- [Codex App Server](https://learn.chatgpt.com/docs/app-server): describes thread/turn identities, `turn/start`, `turn/interrupt` and subsequent completion events. The fetched documentation did not establish a supported attachment contract for the already-running desktop app or reliable live draft/Fast state. No private endpoint was probed.
- [API error codes](https://developers.openai.com/api/docs/guides/error-codes): distinguishes model-overload errors from quota/spend errors and says to honor Retry-After and increase delays. The desktop error representation still needs verification; an API error classification is not proof of how the desktop exposes that error.

The fetched docs do not establish whether a desktop Fast toggle changes an already-running turn. The policy therefore models a confirmed stop, a confirmed switch to Normal, then a continuation. That sequence and the installed UI labels remain **unverified live**.
