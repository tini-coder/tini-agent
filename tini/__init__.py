"""tini-agent — a minimal, transparent, local-first Tini.

Four pillars, one module each:
  harness  → tini/runtime + tini/gateway  (scaffolding around the raw LLM)
  loop     → tini/loop                      (observe → reason → act → repeat)
             tini/graph                     (opt-in structure around the loop — extends this pillar)
  memory   → tini/memory                    (procedural / semantic / episodic)
  ops      → tini/ops + evals/              (trace → eval → gate → release)
"""

__version__ = "0.1.0"
