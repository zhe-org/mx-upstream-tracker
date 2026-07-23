# k8s-upstream-tracker

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
  state.py             # processed-tag persistence (state/processed.json)
  models.py            # RepoConfig / Finding / GraphState schemas
  registry.py          # upstream-tracker.yaml loader + validation
  graph.py             # LangGraph wiring (orchestrator + repo sub-graph)
  main.py              # CLI entrypoint (single tracker pass)
  agents/              # orchestrator, preflight, analyzer, reporter nodes
  tools/               # llm, github, git_ops, cve integrations
upstream-tracker.yaml  # tracked-repo registry (the workflow's input)
state/                 # processed-tag state (processed.json)
tests/                 # skeleton + registry tests
.github/workflows/     # ci.yml (lint+test) + nightly.yml (scheduled run)
```

## Getting started

```bash
uv sync                      # install deps (incl. dev tools)
cp .env.example .env         # then set GH_TOKEN

uv run ruff check .          # lint
uv run pytest                # tests
uv run python -m tracker.main   # build the graph (skeleton pass)
```

## Status

Milestone 0 (scaffolding) is in place. Node bodies arrive with Milestones 2–6.
Every function stub raises `NotImplementedError` tagged with its milestone.
