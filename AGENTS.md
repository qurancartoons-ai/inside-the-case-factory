# Autonomous repository workflow

These instructions apply to the entire repository.

## Working method

For every coding task, work autonomously through this loop:

1. Inspect the repository status and the relevant implementation, configuration, tests, and documentation before editing.
2. Preserve unrelated user changes and make the smallest safe change that solves the task.
3. Run the relevant focused tests while developing.
4. Run the full offline verification commands before declaring the task complete:

   ```bash
   python3 -m unittest discover -s tests -p 'test_*.py'
   python3 -m inside_case_factory health
   ```

5. If scripts, script prompts, script validation, Dutch language checks, or Dutch fixtures changed, also run:

   ```bash
   python3 -m inside_case_factory language-check --fixture all
   ```

6. On failure, read the complete error output, identify and fix the root cause, rerun the focused failing check, and then rerun every required check. Repeat until all checks pass or a genuine blocker requires human input.

## Safety constraints

- Never weaken, skip, mock away, or lower tests, validators, factual-safety gates, approval gates, budget limits, or paid-API confirmations merely to make a check pass.
- Never call paid or external APIs unless the user explicitly authorizes that call and all existing confirmation and budget gates pass.
- Never commit API keys, credentials, secrets, `.env` files, generated project data, API responses, media output, voice-over output, render output, or `.calibration/` output.
- Never overwrite accepted production artifacts as part of testing.
- Do not alter research, media, voice-over, rendering, publishing, or approval behavior unless the task explicitly requires it.
- Keep offline verification deterministic and side-effect-light.

## Definition of done

A coding task is done only when:

- the requested behavior is implemented with focused regression coverage;
- all required offline commands above succeed;
- `git diff --check` succeeds;
- no secret or generated artifact is staged;
- relevant documentation is updated when commands or operator behavior change;
- any genuine remaining blocker is reported with the exact failing command and cause.
