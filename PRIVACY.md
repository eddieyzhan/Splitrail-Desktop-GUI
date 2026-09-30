# Privacy

Splitrail Desktop has no telemetry, analytics, crash reporting, hosted backend, or embedded account credentials. Opening the app does not upload usage. The code repository and a user's private usage repository are separate.

## Local operation

The dashboard reads local usage and optional quota tools. To show hourly charts, it streams normalized per-request statistics from the collector and keeps only timestamp/token/cost aggregates. Session names, project metadata, request identities and unexpected conversation content are discarded in memory; per-request collector records are never written to disk. Source logs remain untouched. Local exports and settings stay on your computer until you choose to share them.

The optional Codex process monitor reads local OS process metadata, including PID, owner, executable, start time, working directory and status. It briefly inspects command arguments to classify the engine, but never retains or displays arguments. Process details and cutoff selections remain in memory and are never exported or synced. Only the chosen cutoff percentages are saved in local preferences. Arming a cutoff explicitly authorizes terminating protected local Codex engines; it starts off each time the app opens. Quota readings remain local and the app never consumes reset credits.

## Public price updates

Automatic price updates download the same public model-price catalogue from this project's GitHub repository for every device, at most daily during usage refresh. They use a plain HTTPS request without GitHub sign-in, authentication tokens, cookies, usage totals or model-specific queries. GitHub receives ordinary connection metadata such as your IP address. Validated prices are cached in `price-catalog.json`; custom rates remain in `model-prices.json` and take priority. Disable **Settings → Model pricing → Automatic price updates** for offline pricing. Demo mode never makes these requests.

## Optional GitHub sync

Connecting GitHub and enabling data sharing are explicit actions. GitHub CLI handles authentication; its browser flow shows a one-time code, never an access token, in the app. The app neither reads credential files nor calls `gh auth token`. GitHub CLI may use a plaintext credential fallback where an OS credential store is unavailable; consult its documentation.

Sync transfers a strict allowlist: daily token and activity counts, hourly token/cost buckets, estimated costs, dates, known tool/model identifiers, and random device identifiers. Unknown identifiers are pseudonymized. Source paths, hostnames, emails, prompts, responses, credentials, raw session IDs, account identifiers and quota data are excluded. GitHub still knows the authenticated account and receives ordinary network metadata. A private repository is access controlled, not end-to-end encrypted.

The app verifies repository privacy before each transfer. Owners control access. Making a repository public outside the app can expose previously committed usage; never change the visibility of a usage repository. The app cannot retroactively hide data published through GitHub itself.

Disconnect removes the local received cache and disables sync. It does not sign out GitHub CLI or delete the remote repository or its history. Remove the private repository through GitHub to delete its stored history, subject to GitHub's retention policies.

## Public distribution

Only source code, synthetic tests/demo data, documentation and build outputs are published. Public screenshots use demo mode. Source archives and application bundles contain no runtime settings, usage logs or original private Git history. Contributions should follow the same boundary; do not attach raw session logs to issues.
