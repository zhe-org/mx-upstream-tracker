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
