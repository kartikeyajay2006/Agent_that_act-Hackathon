# Demo prompts

Paste into the TrueForge chat for the `forgesre` agent (Agents → forgesre → Try), or run
`./scripts/run-agent.sh` to drive the same agent from the terminal.

## Main demo (approve the rollback when TrueForge asks)

```text
Production checkout failures are being reported. Investigate the incident, determine the root cause,
take safe recovery actions, and restore the system.
```

Short version for a live demo:

```text
Checkout is failing in production. Investigate and recover the service.
```

## Failure acceptance test (deny the rollback)

Same prompt. When TrueForge shows the approval request for `rollback_deployment`, click **Deny**.
Expected: no rollback, v2 stays live, the agent acknowledges the denial, does not attempt a workaround,
summarises current risk with fresh numbers, and files the report with status UNRESOLVED.

## Follow-ups worth asking after the run

```text
Show me the evidence that the restart did not fix it.
```

```text
What exactly would have happened if I had denied the rollback?
```

```text
Write the incident report again with the sandbox output quoted.
```
