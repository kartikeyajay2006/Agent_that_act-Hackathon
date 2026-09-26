# Stage model and offline backup

Model selection stays in the local, Git-ignored `.env`; no model vendor or ID is pinned in application code. Choose a
currently available, low-latency model that supports reliable tool calling from the provider configured in TrueForge.
Use that provider's live model catalog rather than relying on a stale model name in a script or recording.

## Configure the stage model

Set these existing variables in `.env` using values for the selected provider:

- `MODEL_PROVIDER`
- `MODEL_ID`
- `MODEL_API_KEY`
- `MODEL_BASE_URL` when the provider requires a custom endpoint
- `MODEL_REASONING_EFFORT` only when the selected model supports/configures reasoning effort

Keep credentials in `.env`; do not put them in source files, command history, screenshots, or the backup recording.
Run `./scripts/setup-trueforge.sh --full` to configure the action-capable stage agent. Setup validates that the selected
model is visible through TrueForge before saving the agent. The separate `forgesre-investigator` remains read-only.

## Capture a backup run

After a successful rehearsal, save a short screen recording of the approved demo path where the team can play it
offline. Keep the recording outside tracked source, and review it for API keys, bearer tokens, private URLs, and
personal account details before sharing. Do not automate approval: show the human approval step in the live or recorded
run. Rehearse the live flow as well; the recording is a venue-network fallback, not evidence that the live setup works.
