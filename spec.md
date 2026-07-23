# Upstream Release Tracker & Report Agent

**Author:** Zhe Yuan

**Status:** Proposal / Draft

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

1. Notice that the release happened or vmware tells us.
2. Merge the new upstream tag into our fork.
3. Deliver it internally.

Currently this is entirely manual, which causes several recurring problems:

- **Easy to miss releases.** Nobody is formally watching every upstream repo
  and tag stream. New patch tags get noticed late. Normally when vmware tells us, we have a tight deadline.
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
2. **Examines each new release** (release notes, diff vs the previous tag we
   track, dependency changes, CVE references) using an LLM.
3. **Produces a consolidated review report** for the team, flagging which
   merges look routine, which need careful review, and which contain
   security-relevant changes.

The workflow does *not* perform the merge itself in v1. It produces the
information a human needs to decide and to merge confidently.

## Architecture

A multi-agent design with an orchestrator, per-repo workers, and a reporter.
Each per-repo worker is itself a small **sub-graph** rather than a single
agent: a preflight check agent that gates the work, and an analyzer agent
that only runs when the preflight surfaces something worth investigating.

```
                +---------------------+
                |  Orchestrator agent |
                |  - repo registry    |
                |  - schedule / tick  |
                |  - dispatch         |
                +----------+----------+
                           |
            spawns one repo sub-graph per repo
            that has a new release since the
            last tracked tag
                           |
        +------------------+------------------+
        |                  |                  |
+-------v-------+  +-------v-------+  +-------v-------+
| Repo subgraph |  | Repo subgraph |  | Repo subgraph |
| kubernetes    |  | containerd    |  | coredns       |
| 1.36.x        |  | 2.0.x         |  | 1.11.x        |
|               |  |               |  |               |
| preflight ─┐  |  | preflight ─┐  |  | preflight ─┐  |
|            v  |  |            v  |  |            v  |
| (analyzer if  |  | (analyzer if  |  | (analyzer if  |
|   flagged)    |  |   flagged)    |  |   flagged)    |
+-------+-------+  +-------+-------+  +-------+-------+
        |                  |                  |
        +------------------+------------------+
                           |
                  per-repo structured findings
                           |
                +----------v----------+
                |   Reporter agent    |
                |  - merge findings   |
                |  - rank by risk     |
                |  - render report    |
                +----------+----------+
                           |
                  +--------v---------+
                  |  Delivery layer  |
                  |  (Mattermost,    |
                  |   email, PR,     |
                  |   dashboard)     |
                  +------------------+
```

### Orchestrator agent

Responsibilities:

- Owns the **repo registry**: a config file listing, for each tracked repo,
  the upstream source, the tracked minor (or branch / version constraint),
  our fork location, and the last tag we have merged.
- On each run (scheduled or triggered), queries upstream sources (GitHub
  Releases API, git tags, RSS feeds) for new tags that match the constraint.
- For every repo that has at least one new qualifying tag, spawns a fresh
  repo sub-graph with a focused prompt and the minimum context it needs.
- Collects one assembled finding per sub-graph and hands them to the
  reporter.
- Persists state: which tags have already been processed, so we never
  double-report.

### Per-repo sub-graph

Spawned on the fly, one per repo that actually has new releases. Instead of
a single agent, each repo worker is a small graph with two nodes and a thin
assembler:

```
   Repo sub-graph (one per repo with a new tag)
   +-------------------------------------------------+
   |  Preflight check agent                          |
   |   - fetch release notes / docs diff             |
   |   - inspect new tag(s) & commit log             |
   |   - scan for CVE references                     |
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

Runs on every new tag. It is the gate for the whole sub-graph. Given the
repo identity, tracked version, and the list of new upstream tags since our
last merged tag, it always performs the following checks:

- **Documentation / release notes.** Fetch upstream release notes and any
  docs diff for the new tag(s).
- **Tag information.** Inspect the tag metadata and the tag-to-tag commit
  log against our last merged tag.
- **CVE scan.** Look for CVE references in release notes, commit messages,
  and dependency manifest changes (e.g. `go.mod`, vendor changes).
- **Trial merge in a throwaway workspace.** Clone our fork branch into a
  temporary working directory on the runner and attempt to merge the new
  upstream tag. The merge does **not** need to succeed — the goal is to
  capture the merge output (conflicting files, failed patch applications,
  obvious build breakage). The temp workspace is deleted afterwards; trial
  merges only ever touch a throwaway local clone, never the real fork. No
  separate sandbox (e.g. LXD) is needed: the workflow runs on a GitHub
  Actions runner, which is already an isolated, ephemeral environment.

The preflight agent then makes a single decision:

- **Clean** — no CVE references, no notable behaviour/doc changes, and a
  clean trial merge. It emits a short "all clear" finding directly and the
  sub-graph is done. The analyzer never runs.
- **Flagged** — anything of note (CVE references, behaviour changes,
  deprecations, or merge conflicts). It passes a **handoff bundle** — only
  the flagged items plus their evidence (relevant diff hunks, CVE ids,
  captured merge output) — to the analyzer.

Keeping the analyzer off the path on quiet releases is what keeps the
common case cheap.

#### Analyzer agent

Only runs when the preflight agent flags something. It receives the handoff
bundle (not the full raw diff) and produces the deep analysis:

- **CVEs** — what each CVE is about, its severity, and which component /
  code path is affected in our fork.
- **Merge conflicts** — which files conflict, why they conflict (e.g. our
  local patches vs upstream changes in the same area), and concrete
  suggestions for how to resolve them.
- **Behaviour changes / deprecations** — what changed, blast radius, and
  what a reviewer should verify.
- **Reviewer guidance** — actionable notes for whoever performs the real
  merge.

#### Assembled finding

Whichever path ran, the sub-graph assembles one structured finding per repo
using the same schema the reporter already consumes, enriched with
`preflight` and (when present) `analysis` blocks:

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

For a clean release the finding is much shorter: `preflight.decision:
clean`, `trial_merge.result: clean`, no `analysis` block, and a one-line
summary. The sub-graph's job is judgement: separate "boring patch release,
just merge" from "read this carefully before merging" — and only spend the
analyzer's tokens on the latter.

### Reporter agent

Takes all per-repo findings and produces:

- A single human-readable report (Markdown) with:
  - A top section ranked by risk: high-risk merges first.
  - A per-repo section with summary, highlights, and dependency notes.
  - Direct links to upstream release notes, diffs, and CVE entries.
- A machine-readable summary (JSON) for downstream automation.
- Optional: a short "TL;DR" suitable for posting into a chat channel.

### Why this split

- The orchestrator stays cheap and deterministic: it is mostly bookkeeping
  and dispatch, not LLM-heavy.
- Each repo sub-graph runs in isolation with only the context it needs,
  which keeps prompts small, cheap, and focused.
- Within a sub-graph, the preflight agent gates the expensive work: on a
  boring patch release it does the cheap checks, runs a trial merge, and
  emits "all clear" without ever waking the analyzer.
- The analyzer only runs when something is actually flagged, and it sees a
  focused handoff bundle (the flagged items plus evidence) rather than the
  full raw diff — so deep analysis stays targeted and affordable.
- The trial merge runs in a throwaway temp workspace on the runner, so we
  get real merge signal (conflicts, patch-apply failures) without any risk to
  the fork — and, because the runner is already isolated and ephemeral, with
  no extra sandboxing infrastructure.
- The reporter sees only structured findings, not raw diffs, so it can
  reason about *the set of releases* without drowning in tokens.
- Sub-graphs are spawned on the fly only for repos that actually have a new
  release, so there is no wasted work in quiet weeks.

## Integration / triggering

A few options, not mutually exclusive. We can start with one and add
others.

### Option A: scheduled GitHub Actions (recommended starting point)

- A workflow runs on a cron (e.g. daily at 09:00 UTC) in a dedicated repo.
- It checks out the repo registry, runs the orchestrator, and posts the
  resulting report as:
  - A new issue or a PR comment in a "release-tracker" repo, and/or
  - A message to a Mattermost channel via webhook, and/or
  - An attached artifact for the dashboard.

Pros: zero new infra, easy to audit (every run is a workflow run with
logs), easy to re-trigger manually.

### Option B: Mattermost bot

- A bot user in our Mattermost workspace exposes slash commands:
  - `/release-tracker run` — run the workflow now and post the report
    in-channel.
  - `/release-tracker status <repo>` — show the last tracked tag and any
    pending unreviewed releases for one repo.
  - `/release-tracker ack <repo> <tag>` — mark a release as reviewed /
    handled, so it stops showing up in the report.
- The bot also posts unsolicited reports on a schedule.

Pros: lives where the team already is; conversational; lets us
acknowledge / dismiss findings without leaving chat.

### Option C: event-driven via upstream webhooks / polling

- Instead of a fixed cron, watch upstream releases via GitHub's release
  events (where possible) or a tight polling loop.
- When a new tag matching a tracked constraint appears, immediately spawn
  the relevant sub-agent and post a focused report for *just that repo*.

Pros: lowest latency; we hear about a release very soon after it lands.
Cons: noisier; more moving parts.

## What the workflow needs as inputs

- A repo registry file, version-controlled, listing for each repo:
  - Upstream URL, tracked minor / branch, our fork URL, last merged tag,
    optional owner / reviewer hints, optional "areas to watch" hints
    (e.g. "kubelet", "CRI", "cgroups v2") that the sub-agent should be
    extra alert about.
- Credentials with read access to upstream repos (public is usually
  enough) and read access to our forks.
- `git` available on the runner to perform trial merges in a temporary
  workspace, plus somewhere to stash the captured merge output as an
  artifact. No dedicated sandbox host is required — the GitHub Actions runner
  is already isolated and ephemeral.
- Optional: access to a CVE database (NVD, GitHub Advisory) for enriching
  dependency findings.
