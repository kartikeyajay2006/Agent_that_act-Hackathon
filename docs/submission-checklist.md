# Submission checklist

Use this immediately before submitting or presenting. It converts the official requirements into checks that are
observable from this repository and demo.

## Technical proof

- [ ] Run `./scripts/demo-ready.sh`; it ends with `all checks passed`.
- [ ] Run `./scripts/eval.sh --scenario approve --runs 2` after the final prompt/model change. Keep the generated
  scorecards locally as recording evidence; do not commit run artifacts containing local session data.
- [ ] In one recorded run, expand a TrueForge `exec` step to show agent-generated Python and its JSON output.
- [ ] In the same run, keep the TrueForge `rollback_deployment` approval panel visible for at least three seconds.
- [ ] Show the completed `approval_brief` argument in that panel: it contains hypothesis, evidence, failed restart,
  blast radius, target readiness and recovery plan.
- [ ] Show `verify_recovery: RECOVERED` and the incident report after Allow.
- [ ] Optional but powerful: record a second short clip where Deny leaves v2 active and the rollback never reaches
  ForgeSRE.

## Three-minute recording

- [ ] Follow [video-script.md](video-script.md); it is timed to 2:55 and keeps TrueForge on screen for more than
  30 seconds.
- [ ] Use a labelled cut such as “30 seconds later” while `verify_recovery` waits; do not pretend the verification is
  instantaneous.
- [ ] Do not show `.env`, keys, browser profiles, internal tabs or private session URLs.
- [ ] Export at 1080p and confirm the tool names, approval button and key metrics are readable.

## Public submission

- [ ] Repository is public and `README.md` starts from `./scripts/setup.sh` then `./scripts/up.sh`.
- [ ] README AI-assistance disclosure is accurate for the actual team workflow.
- [ ] Link the video from the submission/readme once uploaded.
- [ ] Personalise and publish [build-story.md](build-story.md) if entering the community prize; tag the organisers and
  use `#agentsthatact`.
- [ ] Rotate any API key ever pasted into chat, a terminal recording or screenshot. `.env` must remain untracked.

## What to say if judges ask

- **Why not allow automatic rollback?** A rollback changes the version serving every customer; the human retains that
  irreversible decision.
- **How is it enforced?** TrueForge holds the tool call, then ForgeSRE verifies the exact single-use approval before
  switching traffic.
- **How do you know it worked?** `verify_recovery` requires readiness, 20 synthetic checkouts, error rate, p95 and
  database utilisation to meet configured thresholds.
- **Why is this an agent and not a script?** It notices a successful restart did not solve the incident, returns to
  evidence, changes strategy, and then waits for the human at the risk boundary.
