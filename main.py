"""CLI entrypoint for the upstream release tracker.

Runs a single tracker pass: build the LangGraph, invoke it, and (eventually)
emit the report. In the M0 skeleton the graph nodes are placeholders, so this
wires everything up without executing node bodies.
"""

from __future__ import annotations

from config import load_settings
from graph import build_graph


def main() -> None:
    settings = load_settings()
    print(f"k8s-upstream-tracker: model={settings.llm_model} registry={settings.registry_path}")
    # Building the graph validates the wiring even before nodes are implemented.
    build_graph()
    print("Graph built. Node implementations arrive with Milestones 2-6.")


if __name__ == "__main__":
    main()
