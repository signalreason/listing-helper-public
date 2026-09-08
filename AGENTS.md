# Repository guidance

Build and maintain the product described in
[the product plan](docs/ebay-listing-assistant-plan.md).

Accepted records in `docs/decisions/` are authoritative for architectural and product
decisions. Use `PROJECT_STATE.md` for current implementation status and immediate
next work.

For product and architecture constraints that apply only when changing the relevant
area, see [repository constraints](docs/repository-constraints.md).

## Working rules

* Keep changes focused on the smallest useful end-to-end result.
* Preserve existing product behavior unless the task requires changing it.
* Prefer the simplest implementation that satisfies the accepted requirements.
* Do not introduce new infrastructure, frameworks, dependencies, persistence, or
  abstractions without a concrete need.
* Treat uploaded photos and generated private listing data as private.
* Never expose `OPENAI_API_KEY` or other secrets to clients, logs, generated
  artifacts, telemetry, tests, or the repository.
* Do not spend OpenAI API credits during normal tests or validation.
* Mark assumptions as unverified until evidence exists.

## Before changing code

Read:

1. The task and its acceptance criteria.
2. `PROJECT_STATE.md` when current project status matters.
3. `.agents/project-log.md` for known failures, prevention steps, and relevant open
   issues.
4. Applicable accepted records in `docs/decisions/`.
5. Only the relevant sections of the product plan or
   `docs/repository-constraints.md` when the task depends on product requirements or
   architectural boundaries.

Do not reread the complete product plan for routine implementation work when the
necessary behavior is already established by code, tests, accepted decisions, or the
task.

For complex, ambiguous, cross-cutting, or high-risk work, inspect enough of the
canonical artifacts to identify dependencies, conflicts, affected invariants, and
missing acceptance checks before implementation.

## Change workflow

* Use a separate Git worktree and task branch unless the user says the current
  worktree is already isolated for the task.
* Do not inspect, modify, integrate, or delete branches or worktrees created by other
  agents.
* Before implementation, define the intended result, acceptance criteria, relevant
  constraints, verification, and explicit out-of-scope work when these are not
  already clear from the task.
* Check concurrent work for overlap in files, interfaces, schemas, or invariants when
  applicable.
* Implement the smallest coherent change.
* Add or update deterministic tests for changed behavior and relevant failure cases.
* Use a separate review context for high-risk changes when useful.
* Validate before committing.
* Stage only intended files.
* Commit with a descriptive message and push the task branch.
* Confirm the push succeeded and the worktree is clean.

When the task includes integration, confirm the required changes reached remote
`main` before deleting the task branch and worktree.

Prefer corrective commits or `git revert` over rewriting committed history.

### Review comments

* Address P1 comments immediately.
* Assess each P2 comment. If action is warranted, create an issue marked P2.
  Do not fix P2 comments in the current PR. Ignore comments that do not need action.
* Ignore comments that are not marked P1 or P2.
* Acceptance criteria and CI are the production gates. An external review process
  is not a separate gate. Do not start repeated review cycles to obtain approval.

## Validation

Run:

```sh
bin/validate-repo
```

For changed browser flows also run:

```sh
uv run playwright install chromium
uv run pytest -m browser
```

Live OpenAI tests are opt-in and require `OPENAI_API_KEY`.

Do not claim a check passed unless it actually ran successfully.

## Project knowledge

Use `.agents/skills/knowledge-compiler` when work produces reusable project
knowledge.

Record non-trivial task decisions and unexpected failures in
`.agents/project-log.md` while the context is current.

For unexpected failures:

1. Determine and record the verified root cause when possible.
2. Fix the root cause rather than only suppressing the symptom.
3. Add a regression test or deterministic validation when practical.
4. Mark unresolved causes or fixes as unverified and keep them open.

When a manual procedure repeats or is clearly likely to repeat, consider automating
it. Put small deterministic repository utilities in `bin/` when their maintenance
cost is justified.

Before commit or handoff, clean the project log: move durable knowledge to its
canonical artifact, merge duplicates, and remove resolved transient detail.

Keep `PROJECT_STATE.md` current and compressed. Replace superseded status rather than
appending history.

## Canonical artifacts

When information conflicts, prefer:

1. Code and tests for executable behavior.
2. Accepted records in `docs/decisions/`.
3. `docs/ebay-listing-assistant-plan.md` for product requirements and planned
   sequencing.
4. `PROJECT_STATE.md` for current implementation status.

Update an existing canonical artifact instead of creating another source of truth.
