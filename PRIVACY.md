# Privacy

The native Mac Auto-recover preview is a local, in-memory simulation. It does not inspect or control Codex, read chat/draft contents, alter permissions, store recovery settings or send recovery data. Its live switch is unavailable; see [AUTO_RECOVERY.md](docs/AUTO_RECOVERY.md).

Splitrail Desktop has no telemetry, analytics, crash reporting, hosted backend, or embedded account credentials. Opening the app does not upload usage. The code repository and a user's private usage repository are separate.

## Local operation

The dashboard reads local usage and optional quota tools. To show hourly charts, it streams normalized per-request statistics from the collector and keeps only timestamp/token/cost aggregates. Session names, project metadata, request identities and unexpected conversation content are discarded in memory; per-request collector records are never written to disk. Source logs remain untouched. Local exports and settings stay on your computer until you choose to share them.

When historical usage is restored, a private local checkpoint stores allowlisted totals and hashed Codex request identities so repeat scans cannot recount requests or erase recorded history. It stores no prompts, replies, raw session IDs, source paths or credentials. The hashed request identities are never synced; GitHub receives the same aggregate allowlist described below. Back up the checkpoint with your app data if you want to preserve recovered totals.

The Qt interface's optional Codex process monitor reads local OS process metadata, including PID, owner, executable, start time, working directory and status. It briefly inspects command arguments to classify the engine, but never retains or displays arguments. Process details and cutoff selections remain in memory and are never exported or synced. Its armed cutoff terminates selected engines. The native Mac app instead requests an exposed generation Stop action through Accessibility, with the scope described in [TURN_INTERRUPTION.md](docs/TURN_INTERRUPTION.md); it does not terminate engines. Cutoffs start off each time the app opens. Only chosen percentages persist. The app never consumes reset credits.

The native Mac quota worker can read the existing Codex `auth.json` to make a read-only HTTPS request to the same fixed ChatGPT usage endpoint used by quota-axi. Only its current access token and account-routing header are sent to `chatgpt.com`, with certificate validation and no redirects. Credentials are not logged, copied, refreshed, saved by Splitrail, or sent to GitHub. Missing credentials, failed requests or unsupported response schemas use the established Codex app-server fallback. Authoritative reset dates are refreshed separately and cached locally with their observation time; cached dates are not presented as a new reading. Changing the auth file invalidates this cache. Demo mode accesses neither credentials nor accounts.

`usage-scan.sqlite3` stores incremental parser state, normalized usage and hashed file/request identities. It contains no source fragments, conversation text or credentials. `native-cache-v1.json` holds daily dashboard aggregates and quota display data for fast reopening. These local caches are never uploaded as files.

## Public price updates

Automatic price updates download the same public model-price catalogue from this project's GitHub repository for every device, at most daily during usage refresh. They use a plain HTTPS request without GitHub sign-in, authentication tokens, cookies, usage totals or model-specific queries. GitHub receives ordinary connection metadata such as your IP address. Validated prices are cached in `price-catalog.json`; custom rates remain in `model-prices.json` and take priority. Disable **Settings → Model pricing → Automatic price updates** for offline pricing. Demo mode never makes these requests.

## Optional GitHub sync

Connecting GitHub and enabling data sharing are explicit actions. GitHub CLI handles authentication; its browser flow shows a one-time code, never an access token, in the Qt app. Splitrail neither reads GitHub credential files nor calls `gh auth token`. The native Mac app uses the existing CLI sign-in. GitHub CLI may use a plaintext credential fallback where an OS credential store is unavailable; consult its documentation.

Sync transfers a strict allowlist: daily token and activity counts, hourly token/cost buckets, estimated costs, dates, known tool/model identifiers, and random device identifiers. Unknown identifiers are pseudonymized. Source paths, hostnames, emails, prompts, responses, credentials, raw session IDs, account identifiers and quota data are excluded. GitHub still knows the authenticated account and receives ordinary network metadata. A private repository is access controlled, not end-to-end encrypted.

The app verifies repository privacy before each transfer. Owners control access. Making a repository public outside the app can expose previously committed usage; never change the visibility of a usage repository. The app cannot retroactively hide data published through GitHub itself.

Disconnect removes the local received cache and disables sync. It does not remove restored historical checkpoints, sign out GitHub CLI or delete the remote repository or its history. Remove the private repository through GitHub to delete its stored history, subject to GitHub's retention policies.

## Public distribution

Only source code, synthetic tests/demo data, documentation and build outputs are published. Public screenshots use demo mode. Source archives and application bundles contain no runtime settings, usage logs or original private Git history. Contributions should follow the same boundary; do not attach raw session logs to issues.
