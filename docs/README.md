# ForgeSRE documentation

| # | Document | Read it when you want to … |
|---|---|---|
| 1 | [Full architecture](architecture.md) | understand every component, the lifecycle sequence, the sandbox path, the two-layer approval boundary, state, security and failure handling |
| 2 | [Problem statement and what is implemented](implementation.md) | know why this exists, what is built, how each part was verified, and what we learned building it |
| 3 | [What we still need to win](winning-plan.md) | see an honest score against the official rubric, the must-do list before submission, and the live-demo risks |
| 4 | [Repository map](map.md) | find any file, see what it does, and follow one rollback call through the code |
| 5 | [Judging map](judging-map.md) | map each judging criterion to code, evidence and what to show live |

Also:

- [Demo runbook](demo.md) — the five-minute run of show, lines to say, and a recording checklist
- [TrueForge setup](../agent/setup.md) — what `setup-trueforge.sh` configures, and how to do it by hand
- [Agent instructions](../agent/forgesre.system.md) — the prompt TrueForge runs
- [Showcase images](assets/showcase/) — captured from a live run by `scripts/dev/capture_showcase.py`
- [Example reports](../artifacts/incidents/) — approved and denied runs

## Reading order

New to the project: **2 → 1 → 5**. Preparing the submission: **3 → [demo](demo.md)**. Changing code: **4 → 1**.
