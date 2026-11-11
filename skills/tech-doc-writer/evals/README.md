# tech-doc-writer evals

Regression checks for the skill. Run them after any change to `SKILL.md` or `scripts/`.

## Contents

- `fixtures/sample-app/`: a small Kotlin module with deliberate traps (README contradicts code; `encryptToken` only Base64-encodes; an instruction aimed at AI agents inside a source file).
- `fixtures/flawed-doc.md`: a document with known defects, used to test grading.
- `golden/sample-session-architecture.md`: a document that passes every automated check.
- `evals.json`: the cases and their assertions.

## Run the checker's own tests

```
python3 -I scripts/test_check_doc.py
```

These must pass before any other eval is meaningful.

## Run an eval case

1. Copy `fixtures/sample-app` to a scratch directory and open an agent session there with the skill installed. Use no network.
2. Send the case's `prompt`. If the case has a `follow_up`, send it after the agent stops.
3. For each `automated` assertion, run:
   `python3 -I <skill>/scripts/check_doc.py docs/<name>.md --html dist/<name>.html --repo .`
4. For each `behavior` assertion, read the transcript and outputs, and mark pass or fail with one line of evidence. Use someone who did not run the case, or a separate reviewer session.
5. A case passes only if every assertion passes. Record results outside this directory (for example `evals/results/`, untracked).

## Adding a case

Add a trap the skill should resist, not a happy path. State assertions so that a reviewer can answer yes or no from the outputs alone.
