# Dashboard UX & Navigation Repair Report

## Scope and constraints
- Kept the existing dashboard architecture and backend flow intact.
- Did not create a second dashboard.
- Did not remove pipeline functionality.
- Focused on UX/navigation simplification and resilience in the existing dashboard.

## Files changed
- `inside_case_factory/web/dashboard.py`
- `tests/test_dashboard_progress_ux.py`

## What was removed or de-emphasized
- Top navigation options that added route noise in the main header were removed.
- Primary header nav now only shows:
  - `Projecten`
  - `Nieuwe documentaire`
- Technical detail noise is kept behind collapsible details where applicable, instead of being shown as primary content.

## Navigation updates
- Root route now redirects to `/projects`.
- Added explicit `/projects` flow and kept project creation at `/projects/new`.
- Added safe Back behavior helpers:
  - `persist_project_checkpoint(...)`
  - `navigate_back(...)`
  - `back_button(...)`
- Added page-level back button usage across key project workflow pages.

## Projects page improvements
- Projects page now acts as the clear default overview.
- Project cards expose practical status fields and actionable resume/open controls.
- Added visible fields and actions including:
  - `Onderwerp`
  - `Aangemaakt`
  - `Laatst gewijzigd`
  - `Workflowfase`
  - `Voortgang`
  - `Status`
  - `Goedkeuring nodig`
  - `Openen`
  - `Doorgaan`

## Persistence and resume behavior
- Project checkpoint state is persisted immediately at important transition points.
- Back behavior no longer implies cancellation.
- If background work is still active, Back returns a safe message and keeps work intact.
- Resume paths remain available so users can continue from last known state.

## Back vs cancel separation
- Back action: safe navigation and preserved state.
- Cancel/stop action: explicit `Taak stoppen` controls with confirmation prompts.
- Queue item actions for blocked/stalled states include:
  - `Hervatten`
  - `Opnieuw proberen`
  - `Taak stoppen`

## Progress shell and user-facing stage model
- Stabilized and repaired `progress_script` in `DashboardApp`.
- Ensured one valid class-level method definition (no nested/duplicate malformed blocks).
- Progress pipeline uses 8 user-facing stages:
  - `Onderwerp`
  - `Onderzoek`
  - `Feitencontrole`
  - `Script`
  - `Beelden`
  - `Montage`
  - `Eindcontrole`
  - `Voltooid`
- Restored research substatus metrics in the progress area:
  - `bronnen gevonden`
  - `claims in concept`
- Preserved technical events view under `Technische details` details block.

## Friendly error handling
- Progress refresh failure now shows a clear user message with retry button:
  - `Status tijdelijk niet beschikbaar`
  - `Opnieuw proberen`
- Blocked/stalled flow messaging remains user-facing and actionable.

## Mobile/layout consistency
- Kept responsive behavior and existing CSS structure.
- Header/nav and workflow/progress layouts remain mobile-capable.

## Tests added/updated and results
- Updated focused UX test expectations in `tests/test_dashboard_progress_ux.py` for:
  - simplified top nav
  - root redirect to `/projects`
  - stage labels and progress shell behavior
  - project card status fields/actions
  - checkpoint persistence
  - safe Back semantics
- Validation run:

```bash
PYTHONPATH=. pytest -q --cache-clear tests/test_dashboard_progress_ux.py tests/test_dashboard_experience.py
```

- Result: `24 passed`.

## Route smoke test
- Additional in-process smoke validation was executed against the current dashboard routes using `DashboardApp.dispatch(...)`.
- Project slug used during smoke run: `a-dashboard-smoke-test-case`.
- Routes checked (expected `200` or `303`):
  - `GET /`
  - `GET /projects`
  - `GET /projects/new`
  - `GET /projects/a-dashboard-smoke-test-case`
  - `GET /projects/a-dashboard-smoke-test-case/advanced`
  - `GET /projects/a-dashboard-smoke-test-case/reference-intake`
  - `GET /projects/a-dashboard-smoke-test-case/draft-review`
  - `GET /projects/a-dashboard-smoke-test-case/production`
  - `GET /projects/a-dashboard-smoke-test-case/dossier-review`
  - `GET /projects/a-dashboard-smoke-test-case/research-panel`
  - `GET /projects/a-dashboard-smoke-test-case/youtube-draft`
  - `GET /projects/a-dashboard-smoke-test-case/research-data`
  - `POST /projects/a-dashboard-smoke-test-case/back`
- Smoke result: `13/13 passing`.

## Remaining UX limits
- The dashboard still uses a large inline JavaScript block for progress rendering, which is functional but harder to maintain safely.
- Existing deprecation warning remains unrelated to this UX scope:
  - `cgi` module deprecation warning from Python runtime.

## Conclusion
The existing dashboard UI is now simpler, project-first, and safer for users navigating active work. The requested UX/navigation/persistence behavior is implemented without backend redesign or pipeline removal, and targeted regression tests pass.
