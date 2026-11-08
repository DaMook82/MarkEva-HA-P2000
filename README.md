# MarkEva P2000 — experimental Home Assistant fork

> **Experimental pre-release for isolated owner testing only; not proven for installation or migration.** Built primarily for personal use; no support is promised. This is a public fork of [cyberjunky/home-assistant-p2000](https://github.com/cyberjunky/home-assistant-p2000), retaining the `p2000` integration domain. Upstream copyright and MIT terms remain in [LICENSE](LICENSE).

## Status and distribution

The development branch contains a GUI configuration flow, options, a latest-call sensor, a persistent unread queue and a separate bounded archive. It is tested at code level only. Full Home Assistant startup, platform forwarding, browser UI, restart persistence, real feed reception, installation, upgrade, removal, migration and rollback have **not** been verified. No popup, dashboard or historical-import service is included. Do not use this candidate in production or depend on it for emergency information.

This candidate is intended to be distributed as the GitHub pre-release `1.0.24b3` for an isolated owner test. HACS does **not** automatically install the development branch. In HACS (HA 2026.10 and older), open this already-added custom repository, enable the pre-release/beta option if offered, and select version `1.0.24b3` in the download/version selector before confirming. In HA 2026.11 and later use Settings > Marketplace and the repository's Pre-release switch; the installed UI and wording can differ. Custom repositories require a GitHub connection in Marketplace and release metadata may not appear immediately. If the exact version is unavailable, **stop**: do not substitute the default branch. This path has not been exercised in a real sandbox. Installing, adding the integration and restarting are owner actions, not performed by publishing the release. The original upstream YAML instructions are retained as [historical reference](docs/upstream-legacy.md), not instructions for this candidate. The inherited screenshots were removed from this branch because they contain historical incident details; they remain in upstream Git history.

## How selection and delivery are intended to work

The existing RSS source uses unencrypted HTTP, a short sliding window and no reliable GUID; source authenticity, availability and complete reception cannot be guaranteed. The first successful nonempty poll establishes a baseline and emits no historical calls. Subsequent rows lacking a nonempty source time, message or capcode, or a valid timezone-aware time, are skipped. A key derived from source time, message and capcode deduplicates subsequent polls; otherwise identical calls can collide. Rejected calls remain in the bounded seen set, so broadening filters does not replay them while retained. Unknown legacy key variants can replay. A crash around delivery can repeat an event.

For new GUI entries the options include a centre, radius in metres, comma-separated place names, a multi-select service dropdown, optional independently verified fire and Lifeliner capcodes under Advanced, and GRIP radius/fallback. Place-name matching applies as a fallback for absent coordinates and is **not** a municipal boundary. Radius matching requires usable feed coordinates. GRIP matching can include other disciplines. The default radius is zero, so configure selection explicitly; verify every selection against representative fixtures before relying on it. No personal selection is preconfigured. Leave Advanced capcodes empty for ordinary service selection; empty codes do not disable selected services. Fire/Lifeliner codes only add limited recognition, not comprehensive coverage. Imported legacy capcodes are instead restrictive filters; an empty legacy capcode filter does not restrict services. An empty service selection matches no ordinary services for new entries, but permits all disciplines for legacy entries; GRIP is independent for new entries. Imported YAML uses its legacy conjunctive filters and options, not the new GUI place-selection semantics.

A latest sensor, unread queue and archive are separate. The sensor state is limited to 255 characters; full event text remains in the event and queue. Missing location and distance are unknown, not zero. The unread attribute is excluded from Recorder; Recorder is not a complete archive. The store holds up to 50 unread calls and 4,096 seen keys. A full unread queue retries a new call only while it remains in the source. New GUI entries keep at most 2,000 archive records. Imported legacy entries use a 90-day archive window and retain records with unknown archive dates; pruning occurs on new archive writes. The source and received times are distinct. No archive browser is supplied.

The integration emits `p2000_call` and exposes `p2000.acknowledge` (call `id`), `p2000.clear_unread`, and response-only `p2000.get_history` (`offset`, `limit` 1–100). Each accepts `config_entry_id`; it may be omitted only when exactly one entry is loaded. Acknowledge/clear mark calls read without deleting latest or archive. History returns a page, total and retention information. These interfaces have not been validated in a running HA installation.

## YAML import and data safety

The candidate accepts one new entry. A first YAML import retains the exact old name as the legacy sensor unique ID. Thereafter integration options own the settings; later YAML edits are ignored and differences logged. Changing the identity name in options is rejected. A second entry or import is refused, and existing multiple entries are not automatically removed.

If a GUI entry exists first, later YAML import aborts with `identity_conflict`; there is no conversion, merge or identity reassignment. The existing GUI entry can continue running; inspect the HA log because browser presentation is unverified. Stop and decide which identity, settings and data should be retained. Do not remove either configuration to force progress.

An existing shared `p2000_call_queue` is copied only to an empty per-entry store, for exactly one legacy YAML entry, after the owner has verified ownership and explicitly selected the migration option. The original is not intentionally changed. Without that choice, or with multiple entries, setup waits. Unsupported/corrupt data or incompatible storage envelopes stop setup without a deliberate rewrite or rename. Validation checks structural essentials, not the meaning of every historical field. No owner store has been inspected, and concurrent external writers are outside this contract.

## Later isolated owner acceptance — not yet executed

After separate approval and choosing an isolated target with no real notifications or device control:

1. Independently back up and verify YAML, entity registry and all old/new P2000 stores. Record the old sensor identity, configuration, latest call, archive and unread list separately.
2. Agree and verify an actual route to the exact development commit before installing. Record the previous version and its recovery path. Test GUI setup and options, source selection, baseline, multiple synthetic calls, acknowledgements, archive and sensor/event output. Check that no unexpected phone notification occurs.
3. For migration, stop on identity conflict, unclear store ownership, inaccessible options or validation failure. Only for exactly one matching legacy entry and a verified matching old store, select the old-store copy option and save. Compare identity, latest, archive and unread against the snapshot before and after a new fixture.
4. With separate approval, test restart, update and removal; check identity, queue, archive and latest after each. Store cleanup on removal is not implemented; user dashboard bindings and stored data may remain and must be assessed separately. Do not silently delete history.
5. For rollback, preserve **both** diverged stores and all new records. Never overwrite the new store with the old one: there is no automatic merge. Establish a reviewed recovery procedure before changing versions. A retained source copy is not a substitute for an independently verified backup.

Actual results must be recorded before claiming migration, restart or installation works. Real feed reception, browser behavior, Recorder continuity, dashboards, notification delivery and the owner's acceptance remain open.

## Development tests

With Python, pytest, pytest-asyncio, pytest-subtests and compatible Home Assistant dependencies installed in a dedicated development environment, run `python -m pytest -q tests`. The existing suite uses synthetic fixtures and temporary stores, including genuine HA classes and flow/config-entry managers; some entry setups, first refresh and platform forwarding are mocked. It does not start a complete HA instance or contact the real feed. The tested HA version and exact run results should accompany each development report rather than being inferred from this document.

## Privacy

Configuration, incident text, coordinates, capcodes, store files and logs can be sensitive. Review and redact them before sharing. The feed travels over HTTP. No private configuration or production storage is included in this repository. Upstream project history is public and is not rewritten by this branch.
