# mx-upstream-tracker

AI workflow that tracks upstream Kubernetes-related releases on the repos we
fork, analyses each new tag (release notes, tag-to-tag diff, dependency/CVE
changes, ephemeral trial merge), and produces a consolidated, risk-ranked merge
review report. v1 does **not** perform the real merge — it produces the
information a human needs to merge confidently.

See [`spec.md`](spec.md) for the full design and the implementation plan in the
Obsidian vault (`tasks/upstream-release-tracker-plan.md`).

## Stack

- **Python 3.12**, managed with [uv](https://docs.astral.sh/uv/)
- **LangGraph** for the orchestrator + per-repo sub-graphs
- **LLM:** GitHub Copilot (OpenAI-compatible) via `GH_TOKEN`, model `gemini-2.5-pro`
- **Trigger:** nightly GitHub Actions cron (+ manual `workflow_dispatch`)

## Layout

```
tracker/               # application package
  config.py            # settings + secrets (env-driven)
  state.py             # last-scanned watermark (state/processed-<set>.json)
  reports.py           # 7-day report store (reports/report-<set>.json)
  models.py            # RepoConfig / Finding / GraphState schemas
  registry.py          # registry loader + fork baseline (canonical/upstream-version)
  versioning.py        # semver tag parsing / line matching (pre-releases included)
  graph.py             # LangGraph wiring (orchestrator + repo sub-graph)
  main.py              # CLI entrypoint (single tracker pass; takes a release set)
  site.py              # static dashboard generator (reports/ -> index.html)
  notify.py            # Mattermost incoming-webhook notification (nightly delivery)
  agents/              # orchestrator, preflight, analyzer, reporter nodes
  tools/               # llm, github, git_ops, cve integrations
registries/            # one upstream-tracker-<set>.yaml per K8s release set (the input)
state/                 # per-set last-scanned watermark (processed-<set>.json)
reports/               # per-set 7-day reports (report-<set>.json), feeds the dashboard
tests/
.github/workflows/     # ci.yml, nightly.yml (tracker + rolling PR), pr-preview.yml,
                       # pages.yml (main dashboard on gh-pages)
```

A run targets one **release set** (a K8s minor line), passed in dot form:
`python -m tracker.main 1.36` loads `registries/upstream-tracker-1-36.yaml`,
reads each fork's baseline from `canonical/upstream-version` in its release
branch, and — when it finds new tags — updates `state/processed-1-36.json` and
`reports/report-1-36.json` and writes this run's tags to
`artifacts/report-1-36.json`.

## Nightly flow

The nightly workflow never pushes to `main`. When any set finds new tags it
rebuilds the rolling branch `tracker/nightly` from `main` plus the updated
`state/` + `reports/`, opens (or reuses) one PR, and posts to Mattermost with a
link to that PR's dashboard preview. Until the PR merges, later runs start from
the data on that branch, so a tag is reported once. Tags stay on the dashboard
for 7 days after detection.

One-time repo settings:

- **Settings → Pages**: source **Deploy from a branch**, `gh-pages`, root.
  `pr-preview.yml` deploys each PR to `pr-preview/pr-<N>/`; `pages.yml`
  deploys `main`'s dashboard to the root.
- **Settings → Actions → General → Workflow permissions**: **Read and write**.
- Secret `GH_TOKEN`: a PAT that can read the upstream repos and forks and push
  branches / open PRs here (PAT-created PRs trigger CI and the preview).

## Getting started

```bash
uv sync                      # install deps (incl. dev tools)
cp .env.example .env         # then set GH_TOKEN

uv run ruff check .          # lint
uv run pytest                # tests
uv run python -m tracker.main 1.36   # run the tracker for release set 1.36
```

## Status

Milestone 0 (scaffolding) is in place. Node bodies arrive with Milestones 2–6.
Every function stub raises `NotImplementedError` tagged with its milestone.

## Mattermost notifications

After the nightly workflow opens or updates the tracker PR, it posts a brief
summary to a Mattermost channel — which components got new tags, the risk
tally, and links to the PR's dashboard preview and the PR — via an **incoming
webhook**.

Setup (one-time):

1. In Mattermost: open the target channel → **Integrations → Incoming Webhooks
   → Add Incoming Webhook**, pick the channel, and copy the generated URL.
   (If the menu is missing, an admin must enable incoming webhooks in the
   System Console.)
2. In GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**, name `MATTERMOST_WEBHOOK_URL`, value = the webhook URL.

The `notify` job only runs when the run found new tags and no-ops cleanly when
the secret is unset, so it never fails the workflow. To run it by hand against
a run's report artifacts:

```bash
export MATTERMOST_WEBHOOK_URL=...          # or leave unset to dry-run (no-op)
uv run python -m tracker.notify artifacts --preview-url <preview-url> --pr-url <pr-url>
```
