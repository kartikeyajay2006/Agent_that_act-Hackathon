# What we still need to win

An honest gap analysis against the official **Agents That Act** rubric (TrueFoundry × Polaris, 26 Sep 2026). The
self-scores are our own estimate, not the judges'.

## The rubric

| Pts | Criterion | Judges' words |
|---:|---|---|
| 30 | The harness is doing the work | "A judge has to watch TrueForge reach a real tool, run generated code in the sandbox, and hold for a person." |
| 25 | It actually runs | "Someone who has never seen the project should be able to clone it, follow the README, and get it going on their own laptop." |
| 20 | Where it stops | Which actions the agent never takes alone, and how that boundary is defended |
| 15 | A job worth handing over | "Would a real person actually delegate this, and is it an interesting thing to delegate?" |
| 10 | Demo clarity | "Five minutes to show the job, the agent doing it, and where the harness fits." |

Required with the submission: a public repo whose README works on someone else's laptop, code running on TrueForge,
a **disclosure of AI assistants used**, and a **demo video that shows the approval moment** before the irreversible
action.

## Where we stand

| Criterion | Now | Why | After the must-dos |
|---|---:|---|---:|
| Harness doing the work (30) | 26–29 | Real model through TrueForge end to end: tools, **agent-written code in the Daytona sandbox**, native gate with the brief in the approval panel, attested rollback. Scored by `eval.sh`: approve 100, deny 94. | 28–30 |
| It actually runs (25) | 17–20 | `setup.sh` → `up.sh`, `doctor.sh`, `preflight.sh`, `demo-ready.sh`, pinned TrueForge, gateway model. Timed on **one** Fedora laptop only. | 21–24 |
| Where it stops (20) | 18–20 | Two-layer gate, single-use attested approvals, allowlists, rate limit, no shell; every real-model deny run held. | 19–20 |
| Job worth handing over (15) | 11–13 | Real, repetitive on-call toil with a human keeping the one irreversible decision. One incident type. | 12–14 |
| Demo clarity (10) | 3–5 | Video script, runbook, screenshots exist; **video not recorded yet**. | 8–10 |
| **Total** | **~75–87** | | **~88–98** |

## Must-do before submitting (in this order)

| # | Task | Why it matters | Time | Done when |
|---|---|---|---|---|
| 1 | **Run `./scripts/eval.sh` after the final prompt/model change.** Approve, deny and false alarm, scored. | Fresh, repeatable evidence right before judging | 8 min | approve ≥ 90, deny ≥ 90, no safety check failed |
| 2 | **Check the model writes code in the sandbox.** Watch for two `exec` steps (its own script, then the analyzer). If it skips 5a, tighten `agent/forgesre.system.md` and re-run setup. | "run **generated** code in the sandbox" is literally in the rubric | 15–30 min | Session trace shows a script the model wrote |
| 3 | **Fresh-laptop test.** A teammate who has not seen the repo clones it on *their* machine (ideally macOS) and follows only the README. Time it, note every snag, fix the README or scripts. | 25-pt criterion is judged exactly this way | 45–60 min | Up and healthy in < 15 min without help |
| 4 | **Record the video (≤ 5 min).** Follow `docs/demo.md`. Must show: tool calls in TrueForge, the sandbox step, the restart failing verification, the **Allow/Deny panel**, recovery. Add a 20 s Deny clip. | Required deliverable + 10-pt criterion | 45 min | Uploaded, linked in README |
| 5 | **Own the architecture.** Every team member should be able to explain `docs/architecture.md` §3 and §5 without notes. | Rules: AI use is allowed but teams must explain their own architecture | 20 min | Anyone can whiteboard the two-layer gate |
| 6 | **Review the AI-assistance disclosure** in the README and adjust the wording to what the team actually did. | Required by the rules | 5 min | Section accurate |

## Should-do if time allows

| Task | Gain | Effort |
|---|---|---|
| Keep Daytona under its 30 GiB cap: setup deletes stopped sandboxes after 30 min; check `app.daytona.io` before judging | A full disk silently breaks the sandbox step | 2 min |
| Shorter verification for the live demo: `settle_seconds: 20`, `metric_window: 20s` in `config/verification.yaml` | Two verifications currently add ~60 s of waiting to a 5-minute demo | 5 min + rehearse |
| Keep `gpt-4.1-mini` for stage (fast, cheap, reliable tool calls); keep a pre-recorded run as backup | Venue Wi-Fi and model latency are the biggest live risks | – |
| A second incident type (e.g. bad config or memory growth) using the same tools | "Is it a one-trick demo?" objection; 15-pt criterion | 2–3 h |
| Trigger from a schedule or webhook instead of a typed prompt (TrueForge schedules) | Looks like real on-call, not a chat | 1 h |
| Build-story write-up from `docs/implementation.md` → "Problems found while building" | Separate community prize for best build story | 30 min |

## Things we should not claim

- That it works on macOS or Windows — until task 3 is done on such a machine (Windows would need WSL2).
- That the scripted model is the agent. It is a test double; the demo must use a real model.
- That TrueForge local mode is production-grade — it has no login; the attestation reads the local API.

## Live-demo risks

| Risk | Mitigation |
|---|---|
| Model skips a step or calls rollback before trying restart | The gate still holds; rehearse; tighten instructions; keep the recorded video ready |
| Model or venue network slow | Fast model; pre-pulled Docker images (`./scripts/setup.sh` pulls them); backup video |
| Sandbox network restricted | The analyzer arrives through the MCP bridge, not the internet; the agent's own script needs no network either |
| Judge's Linux box lacks bubblewrap | `doctor.sh` says so; install `bubblewrap socat ripgrep` or set `DAYTONA_API_KEY` |
| Port already in use | Change ports in `.env` |
| Metrics look empty right after reset | Reset waits for 45 s of healthy history before returning |
| TrueForge upstream changes | Pinned to `@truefoundry/trueforge@0.2.1` |

## Rules check

| Rule | Status |
|---|---|
| Agent must run on TrueForge | ✅ No LLM client in the repo; TrueForge runs the loop |
| Built during the hackathon | ✅ All commits dated 26 Sep 2026 |
| No API keys in the repo or demo | ✅ `.env` ignored; `setup.sh` generates secrets; don't show `.env` on screen |
| AI assistance disclosed | ✅ README section — review wording (task 6) |
| Demo video with the approval moment | ❌ Task 4 |
