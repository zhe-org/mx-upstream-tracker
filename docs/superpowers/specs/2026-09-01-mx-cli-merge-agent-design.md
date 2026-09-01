# Design: mx-cli merge agent — from "analyse the merge" to "do the merge and raise the PR"

- **Date:** 2026-09-01
- **Status:** proposed
- **Supersedes:** the "v1 does not perform the merge" scope statement in [`spec.md`](../../../spec.md) §Solution
- **Depends on:** `mx-cli` branch `feat/agentic-refactor` (16 commits ahead of `mx-cli@main`)

---

## 1. Context

The tracker today ends at a written opinion. `spec.md` says it plainly:

> **v1 does not perform the merge.**

Every night the orchestrator discovers new upstream tags, the preflight node clones each
fork into a throwaway temp directory, runs `git merge --no-commit --no-ff`, deletes the
workspace, and hands a bundle of text to a single-shot LLM call that writes reviewer notes.
A human then does the actual work from scratch.

`mx-cli` on `feat/agentic-refactor` changes what is possible. That branch deliberately
**deleted** the imperative merge commands (`mx merge-upstream`, `mx ff-merge`,
`mx ci merge-upstream` — commit `5f286de`) and replaced them with two things:

1. Small, deterministic, machine-readable commands (`mx preflight --json`,
   `mx risk --json`, `mx branch parse/resolve --json`, `mx check-deps`, `mx vendor`,
   `mx upstream-version set`).
2. Five agent skills in `.github/skills/` that describe *how an agent should drive git*
   to perform each maintenance workflow, sharing one playbook
   (`common/mixed-source-workflow.md`).

`mx-cli`'s README states the division of labour:

> `mx` owns the parts of mixed-sources maintenance that have a single correct answer
> derivable from the repository state and the naming rules […] Anything that requires
> reading and understanding *code* — resolving a conflict, judging whether an inherited
> patch still applies […] — stays with the agent skills.

This design adopts that split. The tracker becomes the thing that *invokes* the skill on a
schedule, adds the deterministic scaffolding around it, and — the one thing the skills
explicitly refuse to do — pushes the branch and opens the pull request.

## 2. Goals

- On a new upstream tag for a **stable** release branch, land a real merge on a working
  branch, vendored and dependency-checked, and open a PR against the release branch.
- Reuse `mx-cli`'s skills verbatim as the agent's instructions rather than reimplementing
  Canonical's merge policy in the tracker's prompts.
- Keep the report as the primary artifact. The PR is an additional output, not a
  replacement.
- Degrade to today's behaviour — analysis-only — on every failure path.

## 3. Non-goals

- **No rebase path.** Pre-release branches (`edge`/`beta`/`candidate`) are out of scope;
  see §5.
- **No `mx build` / `mx test` in the tracker.** Buildability is verified by the PR's own
  CI. See §9.3.
- **No merging of the PR.** The tracker opens it; a human reviews and lands it.
- **No changes to `mx-cli`.** The tracker consumes it as an installed tool and reads its
  skill files. If a skill needs to change, that is a separate change in that repo.
- **No CVE database enrichment.** `tracker/tools/cve.py:62` (`lookup()`) stays a stub;
  that remains its own milestone.

## 4. What changes, at a glance

| | Today | After |
|---|---|---|
| Repos touched by git | all 19 across both sets | only the 15 `stable`-branch repos in `1.36` |
| Workspace | `mkdtemp` per **tag**, `rmtree` in `finally` | persistent per **repo**, torn down after the repo's tags |
| Merge | `--no-commit --no-ff`, then `--merge abort` | real merge commit on `feat/merge-<tag>` |
| Conflicts | described to a reader | resolved by a ReAct agent following `merge-upstream-stable` |
| Dependency bumps | inferred by the LLM from commit messages | `mx check-deps` against the `sd-tools` ceiling DB, before and after the merge |
| Vendoring | not done | `mx vendor` + `mx vendor --check` |
| Output | `report.md` / `report.json` | same, plus a pushed branch and a PR per landed merge |
| Subgraph nodes | `preflight`, `analyzer` | `preflight`, `merge_agent`, `finish_merge`, `check_deps`, `analyzer`, `publish_pr` |

The top-level orchestrator (`tracker/graph.py:33` `run_orchestrator`) stays plain Python.
Only the per-repo subgraph grows.

---

## 5. Lane classification

Each repo is sorted into one of two lanes **before any git work happens**, based on the
risk encoded in its `canonical_branch`.

`mx branch parse <name> --json` is the classifier. It is safe to call this early because
`parse_branch` (`mx-cli/src/mx/core/branch.py:116`) is pure regex over the branch string —
no git repository, no filesystem, no network. `BRANCH_PATTERN` (line 12) is:

```python
rf"^canonical/(\d+)\.(\d+)(?:-(\d+\.\d+))?/({_RISKS})$"
```

The optional `(?:-(\d+\.\d+))?` handles the tracker's legacy series segment, so
`canonical/1.36-26.04/stable` parses. Applied to the current registries:

| Registry | Branches | Parsed risk | Lane |
|---|---|---|---|
| `1.36` | all 15, e.g. `canonical/2.3-26.04/stable` | `stable` | **merge lane** |
| `1.37` | `canonical/2.4/beta`, `canonical/1.5/beta`, `canonical/3.7/candidate`, `canonical/1.37/candidate` | `beta`, `candidate` | **report-only** |

### 5.1 Report-only lane

Selected when the branch risk is not `stable`, the branch name does not parse, or `mx` is
unavailable. **Nothing is cloned and no git command runs.** Evidence is exactly what the
GitHub REST API already provides: release notes, the tag-to-tag commit log, and the
advisory regex scan (`tracker/tools/cve.py:28`).

`trial_merge.result` takes a new value, `skipped`, and `TrialMerge` gains a
`skipped_reason` (`"branch risk: candidate"`, `"branch name not parseable"`,
`"mx unavailable"`).

Risk assignment is unchanged from today's clean-merge case: `medium` when advisory
references are found, `low` otherwise. The analyzer runs only when advisory references are
found.

**This is a deliberate regression for `1.37`:** those four repos lose their trial-merge
signal. That signal was misleading anyway — a *merge* result on a branch that
`rebase-upstream-prerelease/SKILL.md` says must be *rebased* does not describe the work a
human would actually do. The side effect is that the `1.37` nightly gets dramatically
cheaper, since it no longer clones four repositories to produce a number nobody should act
on.

### 5.2 Merge lane

Selected when the branch risk is `stable`. The full pipeline in §6 runs, subject to the
three rollout switches in §10: with `ENABLE_MERGE_LANE=false` the repo is still cloned and
still trial-merged, but the merge is aborted rather than kept, which is exactly today's
behaviour.

Classification happens in `discover_releases_node` (`tracker/agents/orchestrator.py:32`),
which already constructs `RepoJob`. `RepoJob` gains `branch_risk: str | None` and
`merge_eligible: bool`, so `run_repo_subgraph` knows whether to provision a workspace
before it invokes the subgraph even once.

**Failure default is report-only.** If `mx branch parse` errors, is missing, or times out,
the repo falls to the report-only lane. The tracker never merges a branch it could not
classify.

### 5.3 Multiple tags

Discovery is guaranteed to surface at most one new tag per repo per run, so no
merge-target selection machinery is built. As two lines of insurance against that
guarantee breaking, `run_repo_subgraph` merges only the **newest** tag; any older tags in
the same run take the report-only path. This exists solely to prevent a surprise from
opening several competing PRs against the same base branch. It is not a design axis.

---

## 6. The subgraph

```dot
digraph subgraph {
  rankdir=TB;
  node [shape=box, fontname="monospace"];

  START -> preflight;

  preflight [label="preflight\n\nalways:  release notes, tag-to-tag commit log, advisory scan\nmerge lane only:\n  mx preflight --json          (repo satisfies the mx contract?)\n  mx risk <tag> --check stable (tag may back a stable branch?)\n  mx branch resolve --for-upstream <tag> --risk stable --json\n  mx check-deps origin/<base> <tag>      -> dep_check.pre\n  git checkout -b feat/merge-<tag> origin/<base>\n  git merge -m \"Merge tag '<tag>' into <base>\" <tag>"];

  merge_agent [label="merge_agent   (LangGraph ReAct + guarded bash tool)\n\nsystem prompt = mx-cli common/mixed-source-workflow.md\n              + merge-upstream-stable/SKILL.md\nJOB: resolve conflicts in place, close the merge commit,\n     revert the upstream commits behind any dep_check.pre violation\nNOT its job: vendor, upstream-version, build, test, push"];

  finish_merge [label="finish_merge   (deterministic)\n\nmx upstream-version set <tag>\nmx vendor\nmx vendor --check"];

  check_deps [label="check_deps   (deterministic)\n\nmx check-deps origin/<base> HEAD   -> dep_check.post\nexit 0 -> clean;  exit 1 -> parse the Markdown table"];

  analyzer [label="analyzer   (one-shot LLM, same shape as today)\n\nevidence = preflight + merge outcome + agent report + dep violations"];

  publish_pr [label="publish_pr   (deterministic)\n\nrestore pushable origin URL\ngit push origin feat/merge-<tag>\ngh pr create --base <canonical_branch>"];

  preflight -> merge_agent  [label="conflict\nOR clean + dep_check.pre violations"];
  preflight -> finish_merge [label="clean, no violations"];
  preflight -> analyzer     [label="skipped / error / ineligible\n(and should_analyze)"];
  preflight -> END          [label="skipped, nothing to analyse"];

  merge_agent -> finish_merge [label="resolved"];
  merge_agent -> analyzer     [label="gave up / budget exhausted"];

  finish_merge -> check_deps [label="ok"];
  finish_merge -> analyzer   [label="failed"];

  check_deps -> analyzer   [label="should_analyze"];
  check_deps -> publish_pr [label="clean and nothing to analyse"];

  analyzer -> publish_pr [label="merge landed"];
  analyzer -> END        [label="no merge"];

  publish_pr -> END;
}
```

### 6.1 `should_analyze(state)`

Four nodes route into the analyzer, so rather than one gate node each router calls a
single shared predicate. This keeps every edge explicit in the compiled graph, matching
the existing `route_after_preflight` style (`tracker/agents/preflight.py:132`).

The analyzer runs when **any** of:

- advisory references were found in the notes or commit log;
- the merge agent was involved at all (resolved *or* failed);
- either dependency check reported violations;
- the merge ended in `error` or `ineligible`.

Otherwise the tag is genuinely all-clear and no LLM call is made — the same economics as
today's preflight gate.

### 6.2 `preflight`

Everything it does today, plus — in the merge lane only, in this order:

1. `mx preflight --json` in the workspace. Verifies `canonical/Makefile.canonical` exists
   and declares `build`/`vendor`/`vendor-check`/`test`, `canonical/upstream-version` is
   non-empty, both remotes exist, and the tree is clean. A failure means the fork does not
   satisfy the mx contract: record `merge_state = "ineligible"` with the failing check
   names and route to the analyzer. No merge is attempted.
2. `mx risk <tag> --check stable`. A pre-release tag may not back a stable branch. Failure
   is `ineligible`. In practice this never fires, because `select_new_tags`
   (`tracker/versioning.py:106`) already skips pre-releases — it is defence in depth
   against that filter changing.
3. `mx branch resolve --for-upstream <tag> --risk stable --json`. Yields `<base>`. A
   failure here usually means no stable branch tracks this upstream release, which
   `merge-upstream-stable/SKILL.md:49-52` calls an *onboarding* task, not a merge task —
   record `ineligible` and say so in the finding.
4. `mx check-deps origin/<base> <tag>` → `dep_check.pre`.
5. `git checkout -b feat/merge-<tag> origin/<base>` then
   `git merge -m "Merge tag '<tag>' into <base>" <tag>`.

Classification of step 5 reuses today's logic in `tracker/tools/git_ops.py:124-138`
(rc 0 → `clean`; `CONFLICT` in output → `conflict` + `git diff --name-only
--diff-filter=U`; otherwise `error`). The difference is that on `conflict` the tree is
**left conflicted** for the agent instead of being `git merge --abort`ed, and on `clean`
the merge commit is **kept** instead of being discarded.

**Why `check-deps` runs before the merge.** `merge-upstream-stable/SKILL.md:53-72` uses
the pre-merge reading to decide which upstream commits must be reverted:

> **This is your judgement call**: map each violation back to the upstream commit that
> introduced it and note which commits have to come back out.

That is an input to the agent, not a verification of its output. The post-merge run in
`check_deps` is the verification, and the skill is explicit that both are needed
(`SKILL.md:119-124`).

### 6.3 Routing note: clean merge with dependency violations

The literal instruction for this work was *"if a merge is clean, there is no need to let
the agent do another merge."* This design deviates in one narrow case, and the deviation
is called out here so it can be vetoed on review.

If the merge is clean **but** `dep_check.pre` found violations, the design routes to
`merge_agent` anyway. Its job in that case is not conflict resolution — it is the revert
work in `SKILL.md:86-94`. Without it, a clean merge with violations produces a PR that
breaks a Superdistro ceiling with nobody having decided to accept that. The agent is the
only component that can map a violation back to the upstream commit that caused it.

If this is unwanted, the alternative is to route clean-with-violations straight to
`finish_merge` and rely on the PR body flagging the violations for the reviewer. That is a
one-line change to the router.

### 6.4 `finish_merge` (deterministic, no LLM)

```
mx upstream-version set <tag>     # commits: "chore: set canonical/upstream-version to <tag>"
mx vendor                         # commits "build(vendor): re-vendor" only when dirty
mx vendor --check                 # must be clean
```

This node exists for the **clean** path, which has no agent but still needs these steps
before a PR is openable. Since it must exist anyway, the agent does not duplicate it: the
agent's contract stops at "the merge commit is closed and reverts are applied", and
`mx vendor` sits in the bash denylist so the agent cannot drift into it. One code path,
smaller agent blast radius.

Any non-zero exit sets `merge.vendor_result = "failed"`, records the output, and routes to
the analyzer. No PR.

### 6.5 `check_deps` (deterministic, no LLM)

`mx check-deps origin/<base> HEAD`. Exit 0 → `clean`. Exit 1 → parse the Markdown table
from `mx-cli`'s `deps.format_violation_comment` into structured rows:

```
| go.mod | Package | From | To | Tracked ceiling |
| --- | --- | --- | --- | --- |
| `<path>` | `<module>` | `<old|(added)>` | `<new>` | `<ceiling>` |
```

There is no `--json` on this command, so the parser lives in `tracker/tools/mx.py` and is
covered by unit tests against captured fixture output. If `mx-cli` later grows
`--json`, the parser is the only thing that changes.

Any other failure (credentials, network, missing `git lfs`) → `status = "unavailable"`
with the stderr recorded. This does **not** block the PR; the finding and the PR body both
state that dependency verification was unavailable.

### 6.6 `publish_pr` (deterministic, no LLM)

Runs only when a merge landed and `finish_merge` succeeded. Placed **after** the analyzer
so the PR body can carry the analysis.

```
git remote set-url --push origin <authed-fork-url>   # undo the agent-phase lockout (§8.3)
git push origin feat/merge-<tag>
gh pr create --base <canonical_branch> --head feat/merge-<tag> --title ... --body ...
```

- **Idempotent.** `gh pr list --head feat/merge-<tag> --json url` first; if a PR exists,
  record it as `state: "exists"` and force-push nothing. If the branch exists remotely but
  has no PR, push with `--force-with-lease` and create the PR.
- **Body contents:** tag and base branch, how the merge was achieved (clean / agent-
  resolved), the agent's resolution summary, reverted commits with reasons, the dependency
  violation table, the analyzer's `notes_for_reviewer`, CVE list, and a pointer to the
  artifact log. It states explicitly that `mx build` and `mx test` were **not** run and
  that PR CI is the verification.
- **Failure is non-fatal.** A push or `gh` failure records `PullRequest.state = "failed"`
  with the error and the run continues. The report is still produced.

Authentication is the existing `GH_TOKEN`, via the `x-access-token` URL form already
implemented in `git_ops._authed_url` (`tracker/tools/git_ops.py:40`) and `_scrub`
(line 52) for output redaction.

---

## 7. Data model changes

All in `tracker/models.py`.

```python
MergeResult = Literal["clean", "conflict", "error", "skipped"]          # + "skipped"
MergeState  = Literal["skipped", "ineligible", "clean", "conflict",
                      "agent_resolved", "agent_failed", "error"]
VendorResult = Literal["ok", "failed", "not_attempted"]
DepStatus    = Literal["clean", "violations", "unavailable", "not_attempted"]


class TrialMerge(BaseModel):                      # extended
    environment: str = "runner-tmp-workspace"
    result: MergeResult
    conflicting_paths: list[str] = Field(default_factory=list)
    output_ref: str | None = None
    skipped_reason: str | None = None             # new: why the merge lane was not taken


class MergeAttempt(BaseModel):                    # new
    state: MergeState
    performed_by: Literal["preflight", "agent"] | None = None
    base_ref: str | None = None                   # origin/canonical/2.3-26.04/stable
    working_branch: str | None = None             # feat/merge-v2.3.5
    merge_commit: str | None = None
    conflicting_paths: list[str] = Field(default_factory=list)
    reverted_commits: list[RevertedCommit] = Field(default_factory=list)
    resolution_summary: str | None = None         # the agent's report
    agent_turns: int = 0
    agent_transcript_ref: str | None = None       # artifact path
    vendor_result: VendorResult = "not_attempted"
    ineligible_reasons: list[str] = Field(default_factory=list)
    output_ref: str | None = None


class RevertedCommit(BaseModel):                  # new
    sha: str
    subject: str
    reason: str


class DependencyViolation(BaseModel):             # new
    model_config = ConfigDict(populate_by_name=True)
    go_mod: str
    package: str
    from_: str = Field(alias="from")
    to: str
    ceiling: str


class DepCheck(BaseModel):                        # new
    status: DepStatus = "not_attempted"
    violations: list[DependencyViolation] = Field(default_factory=list)
    error: str | None = None


class DepChecks(BaseModel):                       # new
    pre: DepCheck = Field(default_factory=DepCheck)     # origin/<base>..<tag>
    post: DepCheck = Field(default_factory=DepCheck)    # origin/<base>..HEAD


class PullRequest(BaseModel):                     # new
    state: Literal["created", "exists", "failed", "skipped"] = "skipped"
    url: str | None = None
    number: int | None = None
    branch: str | None = None
    error: str | None = None


class NewTagFinding(BaseModel):                   # extended
    ...
    merge: MergeAttempt | None = None             # new
    dep_checks: DepChecks | None = None           # new
    pull_request: PullRequest | None = None       # new


class RepoJob(BaseModel):                         # extended
    repo: RepoConfig
    new_tags: list[str]
    branch_risk: str | None = None                # new
    merge_eligible: bool = False                  # new


class Workspace(BaseModel):                       # new
    root: Path                                    # <workspace_root>/mx-containerd
    fork_dir_name: str                            # mx-containerd (the release-index key)


class SubgraphState(TypedDict, total=False):      # extended
    repo: RepoConfig
    tag: str
    tag_finding: NewTagFinding
    evidence: EvidenceBundle
    merge_eligible: bool                          # new
    workspace: Workspace | None                   # new
    merge: MergeAttempt                           # new
    dep_checks: DepChecks                         # new
    pull_request: PullRequest                     # new


class EvidenceBundle(BaseModel):                  # extended
    ...
    agent_report: str = ""                        # new: the agent's resolution summary
    dep_violation_table: str = ""                 # new: raw check-deps Markdown
```

`Finding` and its computed fields are unchanged. `AnalyzerOutput` is unchanged — the
analyzer keeps the same structured-output contract; only the human message it receives
grows.

**`TrialMerge` vs `MergeAttempt`.** They overlap, and `TrialMerge` is now the narrower of
the two. It is retained rather than folded into `MergeAttempt` because it is part of the
published `report.json` schema that `tracker/site.py` revalidates across historical runs.
`TrialMerge` remains the answer to *"did the merge apply?"*; `MergeAttempt` is the answer
to *"what did we do about it?"*. The single source of truth for the merge outcome is
`MergeAttempt.state`; `TrialMerge.result` is derived from it.

**`MergeState` values are not all terminal.** `conflict` is transient — it is what
`preflight` writes before routing to the agent, and it is always overwritten with
`agent_resolved` or `agent_failed` before the finding is assembled. It never appears in a
report. The terminal set is `skipped`, `ineligible`, `clean`, `agent_resolved`,
`agent_failed`, `error`, which is what §12.1's summary counts enumerate.

**Backward compatibility.** `tracker/site.py:69` (`load_reports`) revalidates archived
`report.json` files through `Finding.model_validate`. Every new field is optional with a
default, so reports written before this change still load.

---

## 8. The merge agent

### 8.1 Runtime

A LangGraph ReAct agent on the **existing** LangChain/OpenRouter stack — the same
`get_chat_model()` factory the analyzer uses (`tracker/tools/llm.py:16`). No second LLM
provider, no second API key, no new dependency beyond what `langgraph` already ships.

It lives in `tracker/agents/merge_agent.py` and follows the codebase's injection
convention: a keyword-only `model=None` parameter, so tests substitute a fake exactly as
`tests/test_analyzer.py:190` does today.

### 8.2 Prompt construction

The system prompt is assembled at runtime, not authored in this repo:

```
tracker/prompts/merge_agent_system.txt      # tracker-specific framing (see below)
+ <MX_CLI_PATH>/.github/skills/common/mixed-source-workflow.md
+ <MX_CLI_PATH>/.github/skills/merge-upstream-stable/SKILL.md   (frontmatter stripped)
```

Reading the skills from the installed `mx-cli` checkout rather than vendoring copies means
Canonical's merge policy has exactly one home. A new `tracker/tools/skills.py` loads them,
with the same `@cache` + explicit-`FileNotFoundError` pattern as
`tracker/prompts/__init__.py:16`.

`tracker/prompts/merge_agent_system.txt` carries only what the skill cannot know:

- The workspace is already provisioned, remotes configured, tag fetched, working branch
  created, and the merge already attempted. **Do not repeat steps 1–3 of the skill.**
- Scope: resolve conflicts, close the merge commit, apply the reverts implied by the
  supplied dependency violations. **Stop there.** Vendoring, `upstream-version`, building
  and testing are handled by the caller and are unavailable to you.
- `SKILL.md`'s step 6 ("state the remaining manual steps") is replaced by: emit a
  structured report — files resolved and how, hunks omitted and why, commits reverted and
  why, anything a reviewer must check.
- The escalation rule from the playbook (`mixed-source-workflow.md:101-106`) still
  applies: stop on a genuine product decision, not because something "looks tricky". A
  stop is recorded as `agent_failed` and routes to the analyzer.

The skill's "stop before pushing" rule survives verbatim and is enforced structurally
(§8.3), not merely by prompt.

### 8.3 The bash tool

One tool: `bash(command: str) -> str`, returning combined stdout+stderr and the exit code
as a string. Three layers of protection.

**1. Denylist + cwd pin.** `cwd` is the workspace root. The raw command string is matched
against a denylist before execution; a hit returns a refusal *as tool output* so the agent
can adapt rather than crash the run:

| Blocked | Why |
|---|---|
| `git push`, `git tag`, `git remote set-url`, `git config --global` | `publish_pr` owns publication; tagging is `mx release`'s job |
| `gh pr create/merge/close`, `gh release`, `gh api -X POST\|PATCH\|DELETE` | same |
| `mx tag`, `mx release`, `mx lp`, `mx ct` | publication surface |
| `mx vendor`, `mx upstream-version set`, `mx build`, `mx test` | owned by `finish_merge`; keeps one code path |
| `sudo`, `rm -rf /`, absolute paths outside the workspace | blast radius |
| pipes into a shell (`curl … \| sh`, `wget … \| bash`) | arbitrary code |

The denylist is a *fast-feedback* mechanism. Shell parsing is adversarially hard, so it is
not the security boundary.

**2. No push capability.** Before the agent runs, `git remote set-url --push origin
DISABLED` is set in the workspace. `publish_pr` restores the authed URL afterwards. Even
if the denylist is bypassed, a push fails at the transport layer.

**3. Scrubbed environment.** The agent's subprocess environment has `GH_TOKEN`,
`OPENROUTER_API_KEY` and `MATTERMOST_WEBHOOK_URL` removed, and `gh` is left
unauthenticated. The upstream/origin fetch URLs are already primed by `provision`, so the
agent needs no credentials. `git_ops._scrub` (`tracker/tools/git_ops.py:52`) is applied to
all captured output regardless.

**Budgets.** Per-command timeout (`BASH_TIMEOUT_SECONDS`, default 600 — `mx vendor`-scale
operations are slow on large repos); output truncated to `BASH_MAX_OUTPUT_CHARS` (default
20 000) as head + elision marker + tail; ReAct loop capped at `MERGE_AGENT_MAX_TURNS`
(default 40); wall-clock budget per repo `MERGE_AGENT_BUDGET_SECONDS` (default 1800).
Exhausting any budget is `agent_failed`, not a crash.

**Transcript.** Every tool call and result is written to
`<artifact_dir>/<owner>-<repo>-<tag>-agent.log` and referenced by
`MergeAttempt.agent_transcript_ref`. This is the audit trail for a PR a human is asked to
trust.

---

## 9. Workspace management

### 9.1 Provisioning

`tracker/tools/workspace.py` replaces the `mkdtemp`-per-tag behaviour in
`trial_merge_fork` (`tracker/tools/git_ops.py:151`). Provisioned once per repo, in
`run_repo_subgraph`, merge lane only:

```
<WORKSPACE_ROOT>/mx-containerd/           # directory name = fork repo basename
  git clone --single-branch --branch <canonical_branch> <authed fork url> .
  mx setup-upstream
  git fetch upstream tag <tag> --no-tags
  git fetch origin 'refs/heads/canonical/*:refs/remotes/origin/canonical/*'
```

The directory name matters. `mx setup-upstream` looks the repo up in
`mx-cli/src/mx/core/release-index.yaml` **by worktree directory name**. All 15 fork
basenames in the `1.36` registry match the index keys exactly:

```
mx-containernetworking-plugins   mx-containerd   mx-opencontainers-runc
mx-coredns   mx-etcd   mx-golang   mx-k8s-autoscaler
mx-k8s-csi-{external-attacher,external-provisioner,external-resizer,
            external-snapshotter,livenessprobe,node-driver-registrar}
mx-k8s-sigs-cri-tools   mx-kubernetes
```

So naming the clone directory after the fork basename requires zero mapping code. A repo
absent from the index fails `mx setup-upstream` → `merge_state = "ineligible"`.

The clone stays **non-shallow**, for the reason already recorded at
`tracker/tools/git_ops.py:65` — a trial merge needs meaningful merge-base history.

### 9.2 Teardown and disk

The workspace is `rmtree`'d after the repo's subgraph invocations complete, in a `finally`,
preserving today's cleanup guarantee. Artifacts (merge log, agent transcript) are written
to `artifact_dir`, which is outside the workspace and survives.

Disk is the real constraint. A full-history clone of `kubernetes/kubernetes` is several
GB, and `ubuntu-latest` has roughly 14 GB free on `/`. Two mitigations, both in the
nightly workflow (§11):

- `WORKSPACE_ROOT` points at the runner's larger volume rather than the workspace disk.
- `DISPATCH_MAX_WORKERS=1` for release sets that have a merge lane, so at most one large
  clone exists at a time. Repos are processed sequentially and torn down between.

A partial clone (`--filter=blob:none`) would cut this dramatically while keeping full
commit history, but changes merge behaviour to lazy blob fetching. Evaluate it during
implementation as an optimisation; it is not the default.

### 9.3 Why no `mx build` / `mx test`

`merge-upstream-stable/SKILL.md` steps 4–5 call `mx build` and `mx test`, both of which
shell out to `make -f canonical/Makefile.canonical <target>`. For `mx-kubernetes` and
`mx-golang` that is a full Go build and test cycle measured in tens of minutes — nightly,
across 15 repos.

The tracker stops at `mx vendor --check`. Buildability is verified by the PR's own CI,
which is the correct place for it: the result is visible to the reviewer, it does not
consume the tracker's nightly budget, and a red PR is a perfectly good signal. The
deviation from the skill is stated explicitly in the PR body and recorded in the finding
so nobody mistakes an open PR for a tested one.

A Go toolchain is still required for `mx vendor`. The nightly installs a recent stable Go;
a vendoring failure degrades to `finish_merge` failed → analyzer → no PR.

---

## 10. Configuration

New entries in `tracker/config.Settings`, all environment-driven, following the existing
pattern:

| Env var | Default | Purpose |
|---|---|---|
| `MX_CLI_PATH` | unset | Checkout of `mx-cli`, for reading `.github/skills/`. Unset → merge lane disabled. |
| `WORKSPACE_ROOT` | `<root>/workspaces` | Where forks are cloned. |
| `ENABLE_MERGE_LANE` | `false` | Gates the real merge. Off → a stable repo is still cloned and still trial-merged, but with `--no-commit --no-ff` followed by `git merge --abort`, exactly as today. `finish_merge` and `check_deps` do not run. |
| `ENABLE_MERGE_AGENT` | `false` | Gates the `merge_agent` node. Off → a conflict routes straight to the analyzer, as today. |
| `ENABLE_PR` | `false` | Gates `publish_pr`. Off → the merge and vendoring still happen locally and are reported; nothing is pushed. |
| `PR_DRAFT` | `true` | Open PRs as drafts. |
| `MERGE_AGENT_MAX_TURNS` | `40` | ReAct iteration cap. |
| `MERGE_AGENT_BUDGET_SECONDS` | `1800` | Wall-clock budget per repo. |
| `BASH_TIMEOUT_SECONDS` | `600` | Per-command timeout. |
| `BASH_MAX_OUTPUT_CHARS` | `20000` | Tool-output truncation. |

Three independent switches, all defaulting off, each gating exactly one milestone
(§15). With all three off the tracker behaves **identically to today** for the `1.36`
stable repos — same clone, same trial merge, same abort, same report — and the only
behavioural change anywhere is the intended one: `1.37` stops doing git work (§5.1).
Enabling them in order turns on the real merge, then the agent, then publication.

`ENABLE_MERGE_LANE=false` does not fork the code path. The workspace is provisioned the
same way; only the merge invocation differs (`--no-commit --no-ff` + abort, rather than a
real merge commit), so there is no second implementation of cloning or fetching to keep in
sync.

`Settings` gains `require_mx_cli_path()` alongside the existing `require_gh_token()` /
`require_openrouter_key()`.

---

## 11. Nightly workflow

Changes to the `run` job in `.github/workflows/nightly.yml`.

**Python versions coexist.** The tracker is `>=3.12`; `mx-cli` is `>=3.14`. `uv tool
install` builds an isolated environment, so `mx` runs on 3.14 while the tracker runs on
3.12. No conflict.

New steps, after `uv sync`:

```yaml
- name: Check out mx-cli
  uses: actions/checkout@v4
  with:
    repository: canonical/mx-cli
    ref: feat/agentic-refactor        # -> main once merged
    path: mx-cli
    token: ${{ secrets.GH_TOKEN }}

- name: Install mx-cli
  run: |
    uv python install 3.14
    uv tool install ./mx-cli
    mx --help > /dev/null            # smoke test; fail fast on a bad install

- name: Set up Go            # required by mx vendor
  uses: actions/setup-go@v5
  with: { go-version: stable }

- name: Enable private HTTPS access for mx check-deps
  # mx-cli clones git@github.com:canonical/sd-tools (private, gh-pages, git-lfs)
  # for the dependency-ceiling DB. Rewrite SSH -> HTTPS so the existing GH_TOKEN works,
  # avoiding a second credential to manage and rotate.
  run: |
    git lfs install
    git config --global \
      url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf "git@github.com:"
```

New `env` on the `run` job:

```yaml
MX_CLI_PATH: ${{ github.workspace }}/mx-cli
WORKSPACE_ROOT: ${{ runner.temp }}/mx-workspaces
ENABLE_MERGE_LANE: "true"
ENABLE_MERGE_AGENT: "true"
ENABLE_PR: "true"
DISPATCH_MAX_WORKERS: "1"
```

The three `ENABLE_*` values are what M11–M13 flip on, one at a time (§15); M14 ships the
surrounding infrastructure with whichever of them are already on.

The upload-artifact step already globs `artifacts/`, so agent transcripts and merge logs
are collected with no change.

**Permissions.** The `run` job already has `contents: write` for the registry/state commit.
Pushing branches and opening PRs on the `canonical/mx-*` forks uses `GH_TOKEN`, which must
carry `repo` scope on those repositories — a prerequisite, not a workflow change.

**`git lfs install` is required.** `mx-cli`'s dependency DB (`go.dep_tree.db`) is an LFS
object; without it `check-deps` reads a pointer file and fails.

### 11.1 `sd-tools` access

`canonical/sd-tools` is private, and `mx-cli/src/mx/util/deps.py` clones it over SSH
(`git@github.com:canonical/sd-tools.git`). Verified:

```
$ git ls-remote https://github.com/canonical/sd-tools gh-pages
fatal: could not read Username for 'https://github.com': No such device or address
```

The `insteadOf` rewrite above makes `mx-cli`'s SSH URL resolve over HTTPS with the
existing `GH_TOKEN`. This requires `GH_TOKEN` to have read access to `canonical/sd-tools`.
If it does not, `check_deps` records `status: "unavailable"` and the run continues —
degradation, not failure.

---

## 12. Reporting

### 12.1 `report.json`

Additive only: `merge`, `dep_checks` and `pull_request` appear on each `NewTagFinding`.
The `summary` block gains counts:

```json
"summary": {
  "repos": 15, "tags": 3,
  "risk_counts": {"high": 0, "medium": 2, "low": 1},
  "merges": {"clean": 1, "agent_resolved": 1, "agent_failed": 0,
             "ineligible": 0, "error": 0, "skipped": 1},
  "pull_requests": {"created": 2, "exists": 0, "failed": 0, "skipped": 1}
}
```

### 12.2 `report.md`

`_summary_table` (`tracker/agents/reporter.py:84`) gains a **PR** column linking the pull
request, or showing the merge state when there is none.

`_tag_detail` (line 93) gains, between "Trial merge" and "Highlights":

- **Merge** — state, working branch, base, merge commit, conflicting paths, reverted
  commits with reasons, vendor result. In the report-only lane: `skipped — <reason>`.
- **Dependency check** — the violation table, or `clean`, or `unavailable — <error>`.
- **Pull request** — the link, plus a standing note that `mx build`/`mx test` were not run
  and PR CI is the verification.

`render_tldr` (line 200) appends `— N PR(s) opened` when any were.

The reporter stays **pure**: no I/O, no wall-clock. All of this is rendering fields that
the subgraph already populated.

### 12.3 Site and notifications

`tracker/site.py` renders the new blocks and links PRs from the summary table.
`tracker/notify.py`'s `build_message` includes the PR count so the Mattermost message says
what was actually done, not just what was found.

### 12.4 Finalize

`tracker/finalize.py` needs a semantic correction. Today it advances
`current_upstream_tag` on the optimistic assumption recorded at `tracker/models.py:37`:

> Since v1 does no real merge, the finalize step bumps this to the newest reported tag
> (assuming it will be merged before the next upstream tag lands).

That assumption is now wrong in a *specific and worse* way: if a merge failed or a PR was
never opened, advancing the baseline means the next run never re-surfaces the tag, and the
failure is silently dropped.

**New rule:** advance the baseline for a tag only when it was reported *and* was not a
failed merge attempt. Concretely, do not advance when `merge.state` is `agent_failed`,
`error`, or `ineligible`, or when `finish_merge` failed. Those tags stay in the baseline so
the next run retries them and they keep appearing in the report until resolved. The
`skipped` (report-only) and successful cases advance exactly as today.

---

## 13. Failure modes

Every path degrades to a report. Nothing here aborts the run.

| Failure | Recorded as | Consequence |
|---|---|---|
| `mx` not installed / `MX_CLI_PATH` unset | lane = report-only | analysis only; no git work |
| `ENABLE_MERGE_LANE=false` | `merge_state = "skipped"`, `trial_merge.result` as today | today's exact behaviour: trial merge then abort |
| `mx branch parse` fails | lane = report-only, `skipped_reason` | no merge attempted |
| `mx preflight` / `mx risk` / `mx branch resolve` fails | `merge_state = "ineligible"` + reasons | analyzer runs, no PR |
| clone / fetch fails | `merge_state = "error"` | analyzer runs, risk `high`, no PR |
| conflict with `ENABLE_MERGE_AGENT=false` | `merge_state = "conflict"`, merge aborted | analyzer runs, as today |
| agent exhausts turns or wall clock | `agent_failed`, transcript kept | analyzer runs with the partial transcript, no PR |
| agent escalates a product decision | `agent_failed` + its report | analyzer runs, reviewer notes carry the question |
| `mx vendor` fails | `vendor_result = "failed"` | analyzer runs, no PR |
| `mx check-deps` unauthenticated | `dep_check.status = "unavailable"` | **PR still opened**, flagged as unverified |
| push or `gh pr create` fails | `pull_request.state = "failed"` | report still produced; baseline not advanced |
| disk exhausted mid-clone | `merge_state = "error"` | that repo degrades; others unaffected |

---

## 14. Testing

Following the existing conventions: flat `tests/`, plain pytest, `monkeypatch` at named
seams, no network.

**New seams to stub** (mirroring `preflight.gather_evidence` and
`analyzer.get_chat_model`):

- `tracker.tools.mx.run` — the single subprocess entry point for every `mx` invocation.
- `tracker.tools.bash.run_command` — the bash tool's executor.
- `tracker.tools.workspace.provision` — returns a fixture directory.
- `tracker.agents.merge_agent.get_chat_model` — a scripted fake ReAct model.
- `tracker.agents.publish_pr.run_gh` — the `gh` executor.

**New test files:**

| File | Covers |
|---|---|
| `tests/test_lane_classification.py` | every registry branch → correct lane; unparseable names; `mx` missing → report-only; `1.37` fixtures |
| `tests/test_rollout_switches.py` | all three switches off → identical findings to the pre-change behaviour for a stable repo; `ENABLE_MERGE_LANE=false` aborts the merge; `ENABLE_MERGE_AGENT=false` routes a conflict to the analyzer; `ENABLE_PR=false` skips `publish_pr` |
| `tests/test_mx_tool.py` | `check-deps` Markdown parser against captured fixtures (clean, one violation, several, `(added)`); exit-code mapping; `--json` parsing for `preflight`/`risk`/`branch` |
| `tests/test_bash_tool.py` | every denylist entry refused; refusal returned as tool output not an exception; cwd pin; timeout; truncation; env scrubbing |
| `tests/test_merge_agent.py` | scripted conflict resolution; turn cap → `agent_failed`; escalation → `agent_failed`; prompt assembly includes both skill files; transcript written |
| `tests/test_finish_merge.py` | vendor success/failure; `vendor --check` dirty → failed |
| `tests/test_check_deps_node.py` | clean / violations / unavailable |
| `tests/test_publish_pr.py` | create; existing PR → `exists`; push failure → `failed`; body contains dep table + analysis; remote URL restored |
| `tests/test_workspace.py` | directory named after fork basename; teardown in `finally`; teardown after a raise |

**Extended:** `test_preflight.py` (both lanes, the pre-merge dep check, `ineligible`),
`test_orchestrator.py` (`RepoJob` classification fields, workspace lifecycle),
`test_reporter.py` (new MD/JSON blocks), `test_finding.py` (new models round-trip,
old `report.json` still validates), `test_site.py`, `test_notify.py`,
`test_finalize.py` (baseline not advanced on failed merges), `test_skeleton.py`
(6-node graph compiles).

**Integration test:** one end-to-end subgraph run per lane against local fixture git
repositories — a real conflict resolved by a scripted fake model, with `mx` stubbed. This
mirrors the existing `test_flagged_tag_flows_through_subgraph_to_analysis`
(`tests/test_analyzer.py:168`).

---

## 15. Rollout

Both kill switches default off, so each milestone is independently shippable and the
nightly keeps working throughout.

| | Milestone | Ships | Switch flipped |
|---|---|---|---|
| M9  | Lane classification | `mx branch parse` classifier, `RepoJob` fields, `skipped` trial-merge state, report-only lane skipping git entirely. **Only visible change:** `1.37` stops cloning. | — |
| M10 | mx tool layer | `tracker/tools/mx.py` (+ `check-deps` parser), `tracker/tools/workspace.py`, `Settings` additions. Workspace replaces `mkdtemp`; behaviour otherwise unchanged. | — |
| M11 | Real merge, no agent | `preflight` merge lane, `finish_merge`, `check_deps` node, new models, reporter blocks. Clean merges land locally and are reported. | `ENABLE_MERGE_LANE` |
| M12 | Merge agent | `tracker/tools/bash.py`, `tracker/agents/merge_agent.py`, prompt assembly, transcripts. Conflicts resolved locally, still nothing pushed. Verified by reading transcripts. | `ENABLE_MERGE_AGENT` |
| M13 | Publish | `publish_pr`, PR body rendering, finalize correction, site + notify. | `ENABLE_PR` |
| M14 | Nightly wiring | `mx-cli` checkout + `uv tool install`, Go, `git lfs`, `insteadOf` rewrite, `WORKSPACE_ROOT`, `DISPATCH_MAX_WORKERS=1`. | — |

M11 before M12 is deliberate: it proves the deterministic scaffolding — workspace, merge,
vendor, dep check, reporting — before an LLM is given a shell. Each of M11–M13 is
observable for as many nights as needed before the next switch is flipped, and every
switch is independently reversible without a code change.

---

## 16. Open risks

1. **`mx-cli` branch is unmerged.** The nightly pins `feat/agentic-refactor`. If that
   branch is force-pushed or its skill files are restructured, prompt assembly breaks.
   Mitigation: `tracker/tools/skills.py` fails loudly with the expected paths, and the
   merge lane disables itself rather than running with a partial prompt.
2. **`GH_TOKEN` scope.** Needs read on `canonical/sd-tools` and write on all 15
   `canonical/mx-*` forks. Neither is verified today. First real run will reveal gaps as
   `unavailable` / `failed` states rather than crashes.
3. **Disk on `ubuntu-latest`.** Full-history clones of `mx-kubernetes` and `mx-golang` are
   the binding constraint. Sequential processing plus teardown should fit; if not, the
   fallback is a partial clone (§9.2) or a larger runner.
4. **Agent quality is unproven.** `google/gemini-2.5-pro` at temperature 0 resolving real
   merge conflicts in Go codebases is an empirical question. M12 exists specifically to
   measure it from transcripts before any PR is opened. If quality is poor, the failure
   mode is `agent_failed` → analyzer → today's behaviour, which is acceptable.
5. **Cost.** A ReAct loop with up to 40 turns of shell output is materially more expensive
   than one structured call. `MERGE_AGENT_MAX_TURNS` and `BASH_MAX_OUTPUT_CHARS` bound it;
   the merge lane only fires on repos that actually got a new tag, which is a handful per
   night.
6. **Deviation flagged in §6.3.** Routing a *clean* merge with dependency violations to the
   agent contradicts the literal instruction for this work. It is the semantically correct
   behaviour per `merge-upstream-stable/SKILL.md`, but it is a one-line revert if unwanted.
