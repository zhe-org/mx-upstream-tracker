### Upstream Release Tracker & Report Agent — implementation TODOs

**Author:** Zhe Yuan
**Spec:** `k8s-upstream-tracker/spec.md`
**Goal:** Build a multi-agent AI workflow that continuously tracks upstream releases on every repo we fork, analyses each new tag (release notes, diff, dependency/CVE changes, trial merge), and produces a consolidated, risk-ranked review report — v1 does **not** perform the real merge.

**Architecture:** Orchestrator (cheap/deterministic bookkeeping + dispatch) → one per-repo sub-graph (preflight gate → analyzer only if flagged) → reporter (markdown + JSON + TL;DR) → delivery layer.

---

#### Milestone 0 — Decisions & scaffolding

Open questions the spec leaves unspecified; resolve before coding.

- [x] Language/runtime: **Python** (uv-managed, 3.12).
- [x] Agent framework: **LangGraph** for orchestrator + sub-graphs.
- [x] LLM provider/model: **GitHub Copilot** (OpenAI-compatible) via `GH_TOKEN`, model **gemini-2.5-pro**.
- [x] State store for processed tags: **flat JSON file in the git repo** (`state/processed.json`).
- [x] v1 trigger: **GitHub Actions nightly cron** (Option A) + `workflow_dispatch`.
- [x] Create project skeleton, dependency manifest, lint/format/test tooling, CI stub.
- [x] Set up secrets handling: `GH_TOKEN` for LLM + upstream/fork read; LXD host/socket for trial merges (`.env.example`).

---

#### Milestone 1 — Repo registry (config) ✅

The version-controlled input that drives everything.

- [x] Define `upstream-tracker.yaml` format (YAML) and schema: per repo — `name`, `upstream`, `canonical_repo`, `canonical_branch`, `canonical_tag`, `last_incorporated_upstream_ref`. Schema is strict (`extra="forbid"`). The tracked line is implied by the upstream ref (e.g. `v1.14.6` ⇒ track `v1.14.*`), so no separate `tracked_version` field. Duplicate `name`s are allowed (multiple tracked versions of one component get one entry each). Dropped `reviewers`/`areas_to_watch` as unused.
- [x] Implement registry loader + schema validation with clear errors (`tracker/registry.py`: `load_registry()` + `RegistryError`; clear messages for missing file, bad YAML, wrong shape, empty/non-list `repos`, per-entry validation).
- [x] Seed registry with all 15 tracked repos (launchpad forks converted to `https://github.com/canonical/<name>`): `kubernetes/kubernetes`, `containerd/containerd`, `containernetworking/plugins`, `coredns/coredns`, `opencontainers/runc`, `etcd-io/etcd`, `golang/go`, `kubernetes/autoscaler`, `kubernetes-sigs/cri-tools`, and the 6 `kubernetes-csi/*` sidecars.
- [x] Unit tests (`tests/test_registry.py`): seed parses to 15 repos, forks are canonical GitHub URLs; invalid/missing fields, bad YAML, wrong shape, and empty registry all rejected.

> Also restructured the codebase into a `tracker/` package (app modules + `agents/` + `tools/`, with `llm.py` under `tools/`); imports are now absolute `tracker.*` and the entrypoint is `python -m tracker.main`.

---

#### Milestone 2 — Orchestrator agent

Bookkeeping + dispatch; keep it non-LLM and deterministic.

- [ ] Implement upstream release discovery: GitHub Releases API + git tags (RSS optional) per repo.
- [ ] Implement version-constraint matching (only tags newer than `last_incorporated_upstream_ref` on the tracked line implied by it).
- [ ] Implement processed-tag state persistence so releases are never double-reported.
- [ ] Implement dispatch: for every repo with ≥1 new qualifying tag, spawn a fresh sub-graph with minimal focused context.
- [ ] Collect one assembled finding per sub-graph and hand off to reporter.
- [ ] Handle "quiet run" (no new tags) cleanly — no wasted sub-graph spawns.
- [ ] Unit tests: constraint matching, dedupe against state, dispatch fan-out.

---

#### Milestone 3 — Finding schema & assembler

Shared contract between sub-graph and reporter.

- [ ] Define the structured finding schema (per spec YAML): `repo`, `tracked_version`, `last_merged_tag`, `new_tags[]` with `risk`, `summary`, `preflight{}`, `highlights[]`, `dependencies[]`, optional `analysis{}`, `notes_for_reviewer`.
- [ ] Implement thin assembler that produces one finding per repo from whichever path ran (clean vs flagged).
- [ ] Ensure clean-release findings are short: `preflight.decision: clean`, `trial_merge.result: clean`, no `analysis` block, one-line summary.
- [ ] Validation + round-trip tests (serialize/deserialize; reporter can consume both clean and flagged shapes).

---

#### Milestone 4 — Preflight check agent (the gate)

Runs on every new tag; decides clean vs flagged.

- [ ] Fetch upstream release notes / docs diff for the new tag(s).
- [ ] Inspect tag metadata + tag-to-tag commit log vs our last merged tag.
- [ ] CVE scan across release notes, commit messages, and dependency manifest changes (`go.mod`, vendor).
- [ ] Trial merge in an **ephemeral LXD container**: spin container, checkout fork, attempt merge of new upstream tag, capture output (conflicting files, failed patch applies, build breakage). Merge need not succeed.
- [ ] Tear down container afterwards; ensure real fork is never touched; stash captured merge log as an artifact.
- [ ] Decision logic: emit "all clear" finding directly when no CVEs / no notable changes / clean merge; otherwise build a **handoff bundle** (flagged items + evidence: diff hunks, CVE ids, merge output) for the analyzer.
- [ ] Tests: clean path emits short finding & never invokes analyzer; flagged path produces correct handoff bundle. Mock LXD + GitHub in tests.

---

#### Milestone 5 — Analyzer agent (only on flagged)

Deep-dive on the focused handoff bundle (not full raw diff).

- [ ] CVE analysis: what each CVE is, severity, affected component/code path in our fork.
- [ ] Merge-conflict analysis: which files conflict, why (local patches vs upstream), concrete resolution hints.
- [ ] Behaviour-change/deprecation analysis: what changed, blast radius, what a reviewer should verify.
- [ ] Produce actionable reviewer guidance; emit `analysis{}` block for the finding.
- [ ] Tests with representative flagged bundles (CVE-only, conflict-only, behaviour-change, mixed).

---

#### Milestone 6 — Reporter agent

Consumes structured findings only (no raw diffs).

- [ ] Render human-readable **Markdown** report: risk-ranked top section (high-risk first) + per-repo sections (summary, highlights, dependency notes) + direct links to release notes/diffs/CVE entries.
- [ ] Emit machine-readable **JSON** summary for downstream automation.
- [ ] Emit optional short **TL;DR** suitable for a chat channel.
- [ ] Tests: ranking order by risk; links present; clean-only run produces a sensible "nothing risky" report.

---

#### Milestone 7 — Delivery + triggering (Option A first)

- [ ] Implement GitHub Actions scheduled workflow (daily cron, e.g. 09:00 UTC) in a dedicated tracker repo: checkout registry → run orchestrator → publish report.
- [ ] Publish report as issue/PR comment in the tracker repo and attach JSON artifact.
- [ ] Post TL;DR to Mattermost channel via webhook.
- [ ] Ensure LXD host/socket is available to the runner (or documented external runner requirement).
- [ ] Manual re-trigger path (workflow_dispatch).

---

#### Milestone 8 — CVE enrichment (optional)

- [ ] Integrate a CVE source (NVD / GitHub Advisory) to enrich dependency findings with severity/description.
- [ ] Cache lookups; degrade gracefully when the source is unavailable.

---

#### Backlog / future (not v1)

- [ ] Option B — Mattermost bot with slash commands: `/release-tracker run`, `status <repo>`, `ack <repo> <tag>`.
- [ ] Option C — event-driven via upstream webhooks / tight polling for low-latency single-repo reports.
- [ ] Dashboard delivery target.
- [ ] Auto-merge (explicitly out of scope for v1).

---

#### Coverage notes (spec → milestone)

- Problem statement (missed releases, risky patches, dependency context, no consolidated view) → M2 discovery, M4/M5 analysis, M6 report.
- Orchestrator responsibilities → M1 + M2.
- Per-repo sub-graph (preflight gate + conditional analyzer) → M4 + M5.
- Assembled finding schema → M3.
- Reporter (md/json/tldr) → M6.
- Integration Option A/B/C + required inputs (registry, creds, LXD, CVE db) → M0, M1, M7, M8.
