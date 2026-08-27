# Upstream Release Tracker & Report Agent

**Author:** Zhe Yuan

**Status:** v1 implemented (living design)

**Type:** AI workflow automation for fork maintenance

## Problem statement

We maintain a number of internal forks of upstream Kubernetes-related repos,
each pinned to a specific minor version. A non-exhaustive list:

- `kubernetes/kubernetes` (e.g. our fork tracks 1.36.x)
- `containerd/containerd`
- `containernetworking/plugins`
- `coredns/coredns`
- … and others

Every time upstream cuts a new patch release on a tracked minor (e.g. 1.36.6
on top of 1.36.5), we need to:

1. Notice that the release happened or VMware tells us.
2. Merge the new upstream tag into our fork.
3. Deliver it internally.

Currently this is entirely manual, which causes several recurring problems:

- **Easy to miss releases.** Nobody is formally watching every upstream repo
  and tag stream. New patch tags get noticed late. Normally when VMware tells us,
  we have a tight deadline.
- **Patch releases occasionally contain non-trivial changes.** "Patch" does
  not always mean "safe". We need to read release notes every time to spot
  things like behaviour changes, deprecations, or API surface tweaks.
- **Dependency bumps need context.** A bump might be a CVE fix, a routine
  Dependabot update, or a meaningful library swap. Right now we have to
  manually read commit messages and CHANGELOG entries to figure out which.
- **No consolidated view.** When several upstreams release in the same
  window, there is no single place that says "here is what changed across
  all our tracked forks this week, and here is what looks risky."

## Proposed solution

An AI workflow that:

1. **Continuously tracks upstream releases** on every repo we fork.
2. **Examines each new release** (release notes, tag-to-tag commit log,
   dependency changes, CVE / advisory references) — cheaply and
   deterministically first, then with an LLM only when something looks risky.
3. **Produces a consolidated review report** for the team, flagging which
   merges look routine, which need careful review, and which contain
   security-relevant changes.

The workflow does *not* perform the merge itself in v1. It produces the
information a human needs to decide and to merge confidently.

## Release sets

A single run targets **one Kubernetes release set** (e.g. `1.36`), not every
repo at once. Each set is a version-controlled registry
(`registries/upstream-tracker-<set>.yaml`) plus a processed-tag snapshot
(`state/processed-<set>.json`). The set is chosen by the CLI argument
(`python -m tracker.main 1.36`), and the nightly workflow runs a **matrix** over
the sets that have a registry (currently `1.36` and `1.37`; adding `1.33`–`1.35`
is a one-line change). This keeps each run focused and lets component versions
differ per Kubernetes line.

## Architecture

A multi-agent design driven by the release-set registry. The **orchestrator**
does cheap, deterministic bookkeeping and dispatch; each tracked repo that has a
new tag is handled by its own small **sub-graph** (a preflight gate plus an
analyzer that only runs when preflight flags something); a **reporter** renders
the consolidated result, which is then published as a web page and a downloadable
artifact. Repos are processed in parallel, and only the analyzer and reporter
touch an LLM.

```
                    +----------------------------------+
                    |  Release-set registry            |
                    |  upstream-tracker-<set>.yaml     |
                    +----------------+-----------------+
                                     |
                    +----------------v-----------------+
                    |  Orchestrator (deterministic)    |
                    |   - load registry                |
                    |   - discover new tags (GitHub)   |
                    |   - dispatch (parallel)          |
                    +----------------+-----------------+
                                     |
           one repo sub-graph per repo that has a new tag,
           invoked once per new tag, run in parallel
                                     |
        +----------------------------+----------------------------+
        |                            |                            |
+-------v--------+          +--------v-------+          +---------v------+
| Repo sub-graph |          | Repo sub-graph |          | Repo sub-graph |
| kubernetes     |          | containerd     |          | coredns        |
| 1.36.x         |          | 2.3.x          |          | 1.14.x         |
|                |          |                |          |                |
| preflight ─┐   |          | preflight ─┐   |          | preflight ─┐   |
|            v   |          |            v   |          |            v   |
| (analyzer if   |          | (analyzer if   |          | (analyzer if   |
|   flagged)     |          |   flagged)     |          |   flagged)     |
+-------+--------+          +--------+-------+          +---------+------+
        |                            |                            |
        +----------------------------+----------------------------+
                                     |
                        per-repo structured findings
                                     |
                    +----------------v-----------------+
                    |   Reporter (pure, risk-ranked)   |
                    |   - Markdown / JSON / TL;DR      |
                    +----------------+-----------------+
                                     |
                 +-------------------+--------------------+
                 |                                        |
        +--------v---------+                    +---------v----------+
        |  Site generator  |                    |     Finalize       |
        |  combined HTML   |                    |  advance baseline  |
        |  page (Pages)    |                    |  + processed snap  |
        +--------+---------+                    +--------------------+
                 |
        +--------v-----------------------------+
        |  Delivery                            |
        |  GitHub Pages site + downloadable    |
        |  artifact + Mattermost notification  |
        +--------------------------------------+
```

### Orchestrator agent

Cheap, deterministic bookkeeping and dispatch — no LLM. Responsibilities:

- Owns the **repo registry** for the selected release set: a strict,
  schema-validated config listing, for each tracked repo, the upstream source,
  our Canonical fork, the fork's release branch, and the newest upstream tag our
  fork currently sits on (`current_upstream_tag`).
- On each run, queries **upstream GitHub tags** per repo and keeps only tags on
  the tracked line implied by `current_upstream_tag` (same prefix +
  major.minor) that are strictly newer and stable (pre-releases skipped).
  (Releases/RSS feeds were considered but tags cover every tracked repo.)
- For every repo that has at least one new qualifying tag, dispatches a fresh
  repo sub-graph. Repos are processed **in parallel** (thread pool — the work is
  I/O-bound on GitHub / git / the LLM).
- Collects one assembled finding per sub-graph and hands them to the reporter.
- **Dedup is baseline-driven, not a processed store.** The finalize step
  advances `current_upstream_tag` to the newest reported tag, so the next run's
  discovery never re-surfaces an already-reported tag. `processed-<set>.json` is
  a human-readable record of the last run, not a dedup ledger.

> Finalize runs after the report is produced (outside the graph), so it can be
> gated on successful delivery: we never advance the baseline for a report that
> failed to go out.

### Per-repo sub-graph

Dispatched only for repos that actually have new releases, and **invoked once
per new tag** (each tag is judged on its own — one repo can jump several tags).
Each invocation is a small graph: a preflight gate plus an analyzer that only
runs when preflight flags the tag.

```
   Repo sub-graph (one per repo with a new tag; invoked once per tag)
   +-------------------------------------------------+
   |  Preflight check agent (runs on every tag)      |
   |   - fetch release notes / docs                  |
   |   - inspect tag-to-tag commit log               |
   |   - advisory scan (CVE / GHSA / GO / USN)       |
   |   - trial merge in a throwaway temp workspace   |
   |     on the runner, capture output               |
   |        (the merge need not succeed)             |
   |                                                 |
   |   clean?   --> emit "all clear" finding --------+---> finding
   |   flagged? --> handoff bundle --+               |
   +---------------------------------+---------------+
                                     |
                                     v
   +-------------------------------------------------+
   |  Analyzer agent (only runs when flagged)        |
   |   - CVEs: what they are, severity, component    |
   |   - conflicts: what conflicts, why, how to fix  |
   |   - behaviour changes / deprecations            |
   |   - concrete reviewer guidance                  |
   |        --> deep-dive finding                    |
   +-------------------------------------------------+
```

#### Preflight check agent

Runs on every new tag and gates the whole sub-graph. Given the repo identity and
the new upstream tag, it always performs:

- **Documentation / release notes.** Fetch the upstream release notes for the
  tag (empty when the tag has no GitHub release — not an error).
- **Tag information.** Inspect the tag-to-tag commit log against our
  `current_upstream_tag` (commit messages feed the advisory scan and the
  analyzer's evidence).
- **Advisory scan.** Regex-detect security-advisory references in the release
  notes and commit messages — **CVE, GHSA (GitHub), GO (Go vuln DB), and USN
  (Ubuntu)**. (Scanning dependency-manifest changes such as `go.mod`/vendor is a
  future enhancement; authoritative severity enrichment is deferred to the CVE
  milestone.)
- **Trial merge in a throwaway workspace.** Clone our fork branch into a
  temporary directory on the runner, add the upstream remote, fetch just the
  tag, and attempt `git merge --no-commit --no-ff`. The merge need not
  succeed — the goal is to capture what *would* happen: `clean`, `conflict`
  (with the conflicting paths and hunks), or `error`. The workspace is deleted
  afterwards; the real fork is never touched. No dedicated sandbox (e.g. LXD) is
  needed — the GitHub Actions runner is already isolated and ephemeral. (A
  throwaway committer identity is set on the clone so a no-ff merge can record
  its merge commit.)

The preflight agent then makes a single, deterministic, merge-driven decision:

- **Clean** — a clean trial merge with no advisory references. It emits a short
  "all clear" finding (risk `low`) and the sub-graph is done; the analyzer never
  runs.
- **Flagged** — anything of note: a merge `conflict` (risk `medium`), a git
  `error` that means we could not evaluate the merge (risk `high`), or an
  advisory reference on an otherwise-clean merge (risk `medium`). It assembles a
  **handoff bundle** — release notes, commit log, detected advisory refs, and the
  captured merge output + conflict hunks — and passes it to the analyzer.

Keeping the analyzer off the path on quiet releases is what keeps the common
case cheap.

#### Analyzer agent

Only runs when preflight flags a tag. It receives the handoff bundle (not the
full raw diff) and produces the deep analysis via an LLM with structured output:

- **CVEs / advisories** — what each is about, its severity, and which component /
  code path is affected in our fork.
- **Merge conflicts** — which files conflict, why (e.g. our local patches vs
  upstream changes in the same area), and concrete resolution hints.
- **Behaviour changes / deprecations** — what changed, blast radius, and what a
  reviewer should verify.
- **Reviewer guidance** — actionable notes for whoever performs the real merge.

The analyzer may **escalate** risk (e.g. a high-severity CVE) but never
downgrades below the preflight risk. It degrades gracefully: any model/parse
failure keeps the flagged finding and appends a "deep analysis unavailable"
note, so one tag never aborts the run.

#### Assembled finding

Whichever path ran, the sub-graph assembles one structured finding per repo
using the same schema the reporter consumes, enriched with `preflight` and
(when present) `analysis` blocks.

Everything except `repo` lives **per tag**: one repo can jump several tags, and
each is judged on its own. `tracked_version` and `last_merged_tag` are derived
from `repo` (the fork's `current_upstream_tag`), not stored twice. The overall
shape:

```
Finding (one per repo)
├─ repo                RepoConfig  (upstream, fork, branch, current_upstream_tag)
├─ tracked_version     derived from repo.current_upstream_tag  (e.g. "1.36.x")
├─ last_merged_tag     derived (= repo.current_upstream_tag)
└─ new_tags[]          one entry per new upstream tag
   ├─ tag              e.g. "v1.36.6"
   ├─ risk             low | medium | high        (drives the reporter's ranking)
   ├─ summary          one line
   ├─ preflight        (always present)
   │  ├─ docs_reviewed        bool
   │  ├─ cve_refs_found       bool
   │  ├─ trial_merge          {environment, result [clean|conflict|error],
   │  │                        conflicting_paths[], output_ref}
   │  └─ decision             clean | flagged
   ├─ highlights[]     {kind, area|component, detail, cve?, severity?, upstream_ref?}
   ├─ dependencies[]   {name, from, to, reason, cve?}
   ├─ analysis         (present only when preflight flagged)
   │  ├─ cves[]               {cve, severity, affects, detail}
   │  └─ conflicts[]          {path, cause, resolution_hint}
   └─ notes_for_reviewer
```

`cve` fields hold whatever advisory scheme was detected (CVE / GHSA / GO / USN);
the reporter links each to the right database.

A concrete example:

```yaml
repo: kubernetes/kubernetes
tracked_version: 1.36.x
last_merged_tag: v1.36.5
new_tags:
  - tag: v1.36.6
    risk: medium
    summary: >
      Mostly bugfixes; one behaviour change in kubelet eviction defaults.
    preflight:
      docs_reviewed: true
      cve_refs_found: true
      trial_merge:
        environment: runner-tmp-workspace
        result: conflict        # clean | conflict | error
        conflicting_paths:
          - pkg/kubelet/eviction/eviction_manager.go
        output_ref: <captured merge log link/artifact>
      decision: flagged          # clean | flagged
    highlights:
      - kind: behaviour_change
        area: kubelet
        detail: Eviction threshold default changed from X to Y.
        upstream_ref: <PR/issue link>
      - kind: cve_fix
        cve: CVE-2026-XXXX
        component: kube-apiserver
        severity: high
        upstream_ref: <link>
    dependencies:
      - name: golang.org/x/net
        from: vA
        to: vB
        reason: cve_fix
        cve: CVE-2026-YYYY
      - name: github.com/foo/bar
        from: vA
        to: vB
        reason: routine_bump
    analysis:                    # present only when preflight flagged
      cves:
        - cve: CVE-2026-XXXX
          severity: high
          affects: kube-apiserver request auth path
          detail: >
            Bypass under condition Z; our fork exposes the same path.
      conflicts:
        - path: pkg/kubelet/eviction/eviction_manager.go
          cause: >
            Our local eviction patch overlaps upstream's default change.
          resolution_hint: >
            Re-apply our threshold override on top of the new default.
    notes_for_reviewer: >
      Confirm our patches around kubelet eviction still apply cleanly.
```

For a clean release the finding is much shorter: `preflight.decision: clean`,
`trial_merge.result: clean`, no `analysis` block, and a one-line summary. The
sub-graph's job is judgement: separate "boring patch release, just merge" from
"read this carefully before merging" — and only spend the analyzer's tokens on
the latter.

### Reporter agent

Pure and deterministic (no I/O, no wall-clock) — it consumes only structured
findings, never raw diffs. It produces:

- A risk-ranked **Markdown** report: high-risk merges first, then a per-repo
  section with summary, highlights, dependency notes, CVEs, and conflicts, with
  direct links to upstream release notes, the tag-to-tag diff, and advisory
  entries (NVD / GitHub Advisories / Go vuln DB / Ubuntu USN).
- A machine-readable **JSON** summary (risk counts + full findings dump) for
  downstream automation.
- A short **TL;DR** suitable for posting into a chat channel.

The **site generator** then aggregates every release set's JSON into a single,
self-contained **HTML page** (overview totals, a risk-ranked *version-bump* table
of From → To per component, collapsible per-component deep-dive cards, and a risk
filter) for publishing to GitHub Pages.

### Why this split

- The orchestrator stays cheap and deterministic: it is mostly bookkeeping and
  dispatch, not LLM-heavy.
- Each repo sub-graph runs in isolation with only the context it needs, which
  keeps prompts small, cheap, and focused.
- Within a sub-graph, the preflight gate does the cheap, deterministic checks
  (and a real trial merge) and emits "all clear" without ever waking the
  analyzer on a boring patch release.
- The analyzer only runs when something is actually flagged, and it sees a
  focused handoff bundle (flagged items plus evidence) rather than the full raw
  diff — so deep analysis stays targeted and affordable.
- The trial merge runs in a throwaway temp workspace on the runner, so we get
  real merge signal (conflicts, patch-apply failures) without any risk to the
  fork — and, because the runner is already isolated and ephemeral, with no extra
  sandboxing infrastructure.
- The reporter sees only structured findings, not raw diffs, so it can reason
  about *the set of releases* without drowning in tokens.
- Sub-graphs are dispatched only for repos that actually have a new release, so
  there is no wasted work in quiet weeks.

## Integration / triggering

A few options, not mutually exclusive. v1 ships Option A.

### Option A: scheduled GitHub Actions (implemented)

- A workflow runs on a cron (daily at 09:00 UTC) plus `workflow_dispatch`, as a
  **matrix over release sets**. Each set's job checks out the registry, runs the
  orchestrator, and produces the report.
- Delivery in v1:
  - **GitHub Pages web report** — a `publish` job aggregates every set's report
    into one HTML page and deploys it via `actions/deploy-pages` (access-controlled
    when hosted under the Canonical org).
  - **Downloadable artifact** — `report.md` / `report.json` uploaded per set.
  - **Mattermost notification** — a `notify` job (after `publish`) aggregates
    every set's report into one brief message — the components with new tags
    grouped by set (From → To), a risk tally, and a link to the deployed Pages
    report — and posts it to a channel via an **incoming webhook**
    (`MATTERMOST_WEBHOOK_URL`). It stays silent on a quiet run (no new tags) and
    no-ops cleanly when the webhook is unset, so it never fails the workflow.
  - The updated registry (`current_upstream_tag` advanced) and processed-tag
    snapshot are committed back to the repo.
- Deferred: posting the report as an issue / PR comment.

Pros: zero new infra, easy to audit (every run is a workflow run with logs),
easy to re-trigger manually.

### Option B: Mattermost bot (future)

The scheduled, unsolicited push already ships in Option A (the `notify` job's
incoming-webhook message). Option B is the future **interactive** layer on top:

- A bot user in our Mattermost workspace exposes slash commands:
  - `/release-tracker run` — run the workflow now and post the report in-channel.
  - `/release-tracker status <repo>` — show the last tracked tag and any pending
    unreviewed releases for one repo.
  - `/release-tracker ack <repo> <tag>` — mark a release as reviewed / handled so
    it stops showing up in the report.
- The bot also posts unsolicited reports on a schedule.

Pros: lives where the team already is; conversational; lets us acknowledge /
dismiss findings without leaving chat.

### Option C: event-driven via upstream webhooks / polling (future)

- Instead of a fixed cron, watch upstream releases via GitHub's release events
  (where possible) or a tight polling loop.
- When a new tag matching a tracked constraint appears, immediately dispatch the
  relevant sub-graph and post a focused report for *just that repo*.

Pros: lowest latency; we hear about a release very soon after it lands.
Cons: noisier; more moving parts.

## What the workflow needs as inputs

- A **release-set registry** file (version-controlled), listing for each repo a
  strict, schema-validated set of fields: `name` (upstream `owner/repo`),
  `upstream` URL, `canonical_repo` (our fork), `canonical_branch` (the fork's
  release branch), and `current_upstream_tag` (the newest upstream tag our fork
  sits on — the baseline; the tracked line is implied by it). The finalize step
  rewrites this file to advance `current_upstream_tag`.
- **Credentials.** `GH_TOKEN` with read access to upstream repos (public is
  usually enough) and read access to our forks (needed for the trial-merge
  clone), and `OPENROUTER_API_KEY` for the analyzer's LLM
  (OpenRouter, `google/gemini-2.5-pro`).
- Optional: `MATTERMOST_WEBHOOK_URL` (incoming-webhook URL) for the nightly
  Mattermost notification; unset disables the push (the `notify` job no-ops).
- `git` available on the runner to perform trial merges in a temporary
  workspace, plus somewhere to stash the captured merge output as an artifact.
  No dedicated sandbox host is required — the GitHub Actions runner is already
  isolated and ephemeral.
- Optional: access to a CVE database (NVD, GitHub Advisory) for enriching
  advisory findings with authoritative severity (future milestone).
