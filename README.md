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
  state.py             # processed-tag persistence (state/processed-<set>.json)
  models.py            # RepoConfig / Finding / GraphState schemas
  registry.py          # registries/upstream-tracker-<set>.yaml loader + validation
  graph.py             # LangGraph wiring (orchestrator + repo sub-graph)
  main.py              # CLI entrypoint (single tracker pass; takes a release set)
  notify.py            # Mattermost incoming-webhook notification (nightly delivery)
  agents/              # orchestrator, preflight, analyzer, reporter nodes
  tools/               # llm, github, git_ops, cve integrations
registries/            # one upstream-tracker-<set>.yaml per K8s release set (the input)
state/                 # per-set processed-tag snapshots (processed-<set>.json)
tests/                 # skeleton + registry tests
.github/workflows/     # ci.yml (lint+test) + nightly.yml (scheduled run)
```

A run targets one **release set** (a K8s minor line), passed in dot form:
`python -m tracker.main 1.36` loads `registries/upstream-tracker-1-36.yaml` and
writes `state/processed-1-36.json`.

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

After the nightly workflow publishes the combined report to GitHub Pages, it
posts a brief summary to a Mattermost channel — which components got new tags,
the risk tally, and a link to the report — via an **incoming webhook**.

Setup (one-time):

1. In Mattermost: open the target channel → **Integrations → Incoming Webhooks
   → Add Incoming Webhook**, pick the channel, and copy the generated URL.
   (If the menu is missing, an admin must enable incoming webhooks in the
   System Console.)
2. In GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**, name `MATTERMOST_WEBHOOK_URL`, value = the webhook URL.

The `notify` job no-ops cleanly when the secret is unset or the run is quiet
(no new tags on any set), so it never fails the workflow. To run it by hand
against downloaded artifacts:

```bash
export MATTERMOST_WEBHOOK_URL=...          # or leave unset to dry-run (no-op)
uv run python -m tracker.notify _reports --page-url https://<pages-url>/
```
