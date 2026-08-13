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
- [x] Set up secrets handling: `GH_TOKEN` for LLM + upstream/fork read (`.env.example`). (No LXD host/socket needed — trial merges run in a temp workspace on the already-isolated GitHub Actions runner.)

---

#### Milestone 1 — Repo registry (config) ✅

The version-controlled input that drives everything.

- [x] Define `upstream-tracker.yaml` format (YAML) and schema: per repo — `name`, `upstream`, `canonical_repo`, `canonical_branch`, `current_upstream_tag`. Schema is strict (`extra="forbid"`). The tracked line is implied by the tag (e.g. `v1.14.6` ⇒ track `v1.14.*`), so no separate `tracked_version` field. Duplicate `name`s are allowed (multiple tracked versions of one component get one entry each). Dropped `reviewers`/`areas_to_watch` as unused. Dropped `canonical_tag` — since v1 does no real merge, we never learn the new fork tag; the fork's version is implied by `current_upstream_tag`. Renamed `last_incorporated_upstream_ref` → `current_upstream_tag` (less confusing): it's the newest upstream tag our fork sits on, advanced by the finalize step (assume the reported tag merges before the next upstream tag lands). The registry header comment block was dropped since the file is now machine-rewritten by finalize.
- [x] Implement registry loader + schema validation with clear errors (`tracker/registry.py`: `load_registry()` + `RegistryError`; clear messages for missing file, bad YAML, wrong shape, empty/non-list `repos`, per-entry validation).
- [x] Seed registry with all 15 tracked repos (launchpad forks converted to `https://github.com/canonical/<name>`): `kubernetes/kubernetes`, `containerd/containerd`, `containernetworking/plugins`, `coredns/coredns`, `opencontainers/runc`, `etcd-io/etcd`, `golang/go`, `kubernetes/autoscaler`, `kubernetes-sigs/cri-tools`, and the 6 `kubernetes-csi/*` sidecars.
- [x] Unit tests (`tests/test_registry.py`): seed parses to 15 repos, forks are canonical GitHub URLs; invalid/missing fields, bad YAML, wrong shape, and empty registry all rejected.

> Also restructured the codebase into a `tracker/` package (app modules + `agents/` + `tools/`, with `llm.py` under `tools/`); imports are now absolute `tracker.*` and the entrypoint is `python -m tracker.main`.

> **Per-release-set registries.** A run now targets **one** K8s release set instead of everything at once. The single `upstream-tracker.yaml` moved to `registries/upstream-tracker-<set>.yaml` (one file per set) and the state snapshot to `state/processed-<set>.json`. `python -m tracker.main <set>` takes a **required** release set in **dot form** (e.g. `1.36`); the filename uses **dash form** (`1-36`). Selection flows via a `RELEASE_SET` env var that `main` sets from the arg and every `load_settings()` reads (no signature churn across the 6 call sites that reconstruct settings from env). Explicit `TRACKER_PATH`/`STATE_PATH` still override (tests). Invalid format or an unknown set exits with a clear error listing available sets. Only the `1.36` registry exists today; others are added when their branch/tag data is known. The nightly workflow passes `1.36` for now (a matrix over `registries/` lands with M7).

---

#### Milestone 2 — Orchestrator agent ✅

Bookkeeping + dispatch; keep it non-LLM and deterministic.

- [x] Implement upstream release discovery: GitHub tags API per repo (`tracker/tools/github.py::list_tags`, paginated, `GH_TOKEN` auth). Releases/RSS deferred — tags cover every tracked repo.
- [x] Implement version-constraint matching (`tracker/versioning.py`): only tags newer than `current_upstream_tag` on the tracked line implied by it. Prefix-aware (`v`/`go`/`cluster-autoscaler-`), skips pre-releases (`rc`/`alpha`/`beta`), ignores unparseable tags, sorts oldest→newest.
- [x] Implement processed-tag record (`tracker/state.py`): `save_processed` overwrites `processed.json` with the last run's reported tags — a **human-readable record** to read alongside the report. Not used for dedup: uniqueness is guaranteed by `current_upstream_tag` in the registry (advanced by finalize), so discovery's `select_new_tags` never re-surfaces a reported tag.
- [x] Implement dispatch: one `RepoJob` per repo with ≥1 new qualifying tag → one sub-graph invocation (`graph.dispatch` → `run_repo_subgraph`), run **in parallel** across repos. Sub-graph body (preflight/analyzer) is M4/M5, so `run_repo_subgraph` emits a **shell finding** for now.
- [x] Collect one assembled finding per sub-graph into `state['findings']` and hand off to reporter.
- [x] Handle "quiet run" (no new tags) cleanly — empty `jobs`, no sub-graph spawns, finalize is a no-op.
- [x] Unit tests: constraint matching (`tests/test_versioning.py`), discovery + dedupe + quiet run + dispatch fan-out + finalize (`tests/test_orchestrator.py`).

> **Finalize step** (`tracker/finalize.py`, run at end of `main`): writes the `processed.json` snapshot **and** advances `current_upstream_tag` in `upstream-tracker.yaml` to the newest reported tag per repo (via `registry.save_registry`), so the next nightly run starts from the advanced baseline. Kept out of the graph so M7 can gate it on successful delivery. **M7 implication:** the nightly job must commit the updated `upstream-tracker.yaml` + `processed.json` back to the repo.

> **Graph shape:** the top-level `build_graph()` was removed. `graph.py` owns the sub-graph and its invocation (`build_repo_subgraph`, `run_repo_subgraph`, `dispatch`, and the `run_orchestrator` pipeline); `orchestrator.py` is pure discovery (`load_tracker_node`, `discover_releases_node`) with no sub-graph knowledge, so the dependency flows one way (`graph` → `orchestrator`) with no import cycle. LangGraph is reserved for the per-repo sub-graph (preflight→analyzer conditional routing) where it earns its keep; dispatch will invoke that compiled sub-graph per repo from M4.

> **Finding model:** holds the whole `RepoConfig` as `repo` (not flattened fields), so preflight's trial merge has `canonical_repo`/`canonical_branch`/`upstream` and the reporter derives `tracked_version`/`last_merged_tag` from `current_upstream_tag`.

> **Parallel dispatch:** repos are processed concurrently via a `ThreadPoolExecutor` (repo jobs are I/O-bound — GitHub / git trial merge / LLM — so threads give real concurrency and the GIL is released during those waits; a process pool would add pickling pain for no benefit). Pool size is capped by CPU count (each trial merge is heavy) and overridable via `DISPATCH_MAX_WORKERS`. `map` preserves input order, so findings stay deterministic.

---

#### Milestone 3 — Finding schema & assembler ✅

Shared contract between sub-graph and reporter.

- [x] Define the structured finding schema (typed pydantic models in `tracker/models.py`): `Finding` holds `repo` (full `RepoConfig`) + `new_tags[]` of `NewTagFinding` (`tag`, `risk`, `summary`, `preflight{docs_reviewed, cve_refs_found, trial_merge{environment, result, conflicting_paths, output_ref}, decision}`, `highlights[]`, `dependencies[]`, optional `analysis{cves[], conflicts[]}`, `notes_for_reviewer`). `tracked_version` / `last_merged_tag` are `@computed_field`s derived from `repo.current_upstream_tag` (still serialized, never stored twice). `Dependency.from_` uses a `from` alias (Python keyword).
- [x] Implement thin assembler (`tracker/assembler.py`): `assemble_finding(repo, new_tags)` (one finding per repo, tags sorted oldest→newest for determinism) + `clean_tag_finding(...)` which encodes the clean-is-short invariant, + `pending_tag_finding(...)` used by the M2 dispatch shell.
- [x] Ensure clean-release findings are short: `clean_tag_finding` forces `preflight.decision: clean`, `trial_merge.result: clean`, no `analysis` block, one-line summary.
- [x] Validation + round-trip tests (`tests/test_finding.py`): serialize/deserialize both clean and flagged shapes; computed fields present in `model_dump`; `Dependency` alias emits `from`/`to`; assembler sorts tags.

> The M2 dispatch shell (`run_repo_subgraph`) now emits schema-valid **pending** tag findings via `pending_tag_finding` until the real preflight/analyzer nodes (M4/M5) produce them.

---

#### Milestone 4 — Preflight check agent (the gate) ✅

Runs on every new tag; decides clean vs flagged. **v1 decision is deterministic and merge-driven** (LLM judgement + CVE scan deferred — see notes).

- [x] Fetch upstream release notes / docs diff for the new tag(s) (`tracker/tools/github.py::get_release_notes`, empty string when a tag has no GitHub release).
- [x] Inspect tag metadata + tag-to-tag commit log vs our last merged tag (`github.py::compare_tags` → `{total_commits, commit_messages, files}`).
- [ ] ~~CVE scan across release notes, commit messages, and dependency manifest changes~~ — **deferred** (descoped by request; lands with the analyzer/CVE work, M5/M8). `preflight.cve_refs_found` is always `False` in M4.
- [x] Trial merge in a **throwaway temp workspace on the runner** (`tracker/tools/git_ops.py`): `clone_fork` (single-branch, non-shallow so the merge-base is meaningful) → `trial_merge` (add upstream remote, fetch the tag, `git merge --no-commit --no-ff`, classify `clean|conflict|error` off git's `CONFLICT` marker, list unmerged paths via `--diff-filter=U`, `merge --abort`). Never raises on conflict.
- [x] Clean up the temp workspace afterwards (`trial_merge_fork` manages a `mkdtemp` workspace and `rmtree`s it in a `finally`); the real fork is never touched; the captured merge log is written to `artifact_dir` and referenced via `TrialMerge.output_ref`.
- [x] Decision logic (`tracker/agents/preflight.py::assess_tag`): clean merge → short all-clear finding (`clean_tag_finding`, risk low); conflict → flagged/medium; git error → flagged/high (`flagged_preflight_finding`, `analysis` left `None` for the analyzer). Evidence (notes/compare) feeds the summary, not the decision.
- [x] Tests (all offline; mock `subprocess` / GitHub / `trial_merge_fork`): `tests/test_git_ops.py` (classifier + cleanup + no-raise), `tests/test_github_tool.py` (notes/compare), `tests/test_preflight.py` (clean-short, conflict-medium, error-high, routing), plus `tests/test_finding.py::test_flagged_preflight_finding_shape` and the sub-graph fan-out test in `tests/test_orchestrator.py`.

> **Sub-graph now runs for real.** The per-repo sub-graph is invoked **once per tag** on a new `SubgraphState` (`repo`, `tag`, `tag_finding`); `graph.run_repo_subgraph` collects the per-tag findings and assembles one repo finding. Compiled once and reused. `preflight_node`/`route_after_preflight` implement the gate; `route` sends `flagged → analyzer`, `clean → assemble(END)`.

> **Analyzer is an M4 passthrough.** `analyzer_node` operates on `SubgraphState` but is a no-op for now (preflight already emits a complete, analysis-free flagged finding); M5 replaces its body with the real CVE / conflict / behaviour-change analysis. This keeps the conditional routing live and tested today.

> **Deferred from the spec's preflight:** the LLM behaviour-change/deprecation judgement and the CVE scan. M4 flags purely on trial-merge outcome, which is cheap, deterministic, and fully offline-testable; the richer judgement arrives with the analyzer (M5) and CVE enrichment (M8).

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
- [ ] Ensure `git` is available on the runner for trial merges (default on GitHub-hosted runners; no LXD/external host required).
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
- Integration Option A/B/C + required inputs (registry, creds, CVE db) → M0, M1, M7, M8.
