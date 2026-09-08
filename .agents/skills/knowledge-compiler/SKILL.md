---
name: knowledge-compiler
description: Complete repository work while promoting reusable knowledge into the narrowest durable artifact. Use when a task produces or changes project conventions, architectural decisions, data contracts, repeated workflows, deterministic procedures, tests, or current implementation state. Do not use to persist one-off observations, speculative ideas, conversation summaries, or facts already owned by a canonical artifact.
---

# Knowledge Compiler

Complete the requested task first. Promote knowledge only when doing so prevents
meaningful reacquisition, inconsistency, or repeated manual work.

## Workflow

1. Read `AGENTS.md`, `PROJECT_STATE.md`, `.agents/project-log.md`, and the task-relevant
   canonical files before creating an artifact. Apply relevant known prevention steps
   and open issues from the log.
2. Complete the requested work within the current product plan and repository
   constraints.
3. Promote a finding only if at least one condition holds:
   - It is likely to be reused.
   - Reacquiring it requires meaningful analysis or research.
   - Inconsistent application could create defects.
   - It defines a repeated workflow.
   - It can be executed or validated mechanically.
   - It is an accepted project decision or invariant.
4. Update an existing canonical artifact instead of creating an overlapping one.
5. Choose the narrowest destination:
   - Executable behavior: production code or `bin/`.
   - Required behavior: automated test or validation.
   - Data contract: schema or typed application code.
   - Runtime defaults: application configuration.
   - Repository-wide convention: `AGENTS.md`.
   - Accepted product or architecture decision: `docs/decisions/`.
   - Stable domain fact: `docs/knowledge/`.
   - Current objective, blockers, and next steps: `PROJECT_STATE.md`.
   - Repeated judgment-based workflow: `.agents/skills/<name>/SKILL.md`.
6. Keep instructions and indexes concise. Link to detailed canonical material rather
   than copying it.
7. Validate new artifacts with `bin/validate-repo` and task-specific tests.
8. Update `PROJECT_STATE.md` only when implementation state, constraints, blockers, or
   immediate next actions changed.
9. Review and compact `.agents/project-log.md` as required by `AGENTS.md`. Make sure
   that each unexpected failure has a root cause or is clearly marked `unverified`,
   and that durable prevention has a canonical owner.
10. Follow the `AGENTS.md` commit policy: commit all validated in-scope changes before
   handoff and confirm the worktree is clean.

## Project precedence

The current eBay Listing Assistant product plan and accepted decision records override
generic repository-layout advice. Preserve these invariants:

- Ease of use is the first priority.
- The product is a responsive, browser-first upload-to-listing workflow for two
  trusted household users; it is not an MVP or pilot program.
- OpenAI calls use an operator-owned server secret; users never bring credentials.
- The key is accessed only through the server's `OPENAI_API_KEY` runtime environment
  variable when a request needs it and is never copied or exposed elsewhere.
- The baseline is one deployable application with no Kafka or distributed job system.
- eBay API integration waits until the existing workflow is smooth and reliable.
- The users are eBay resellers, especially apparel sellers; current upload limits
  match eBay and pricing guidance comes from the LLM.
- The app runs on DigitalOcean App Platform with a small PostgreSQL account store
  within a $20/month combined infrastructure and OpenAI API budget.
- Individual email/password accounts replace invite codes. The owner creates users;
  the server generates and displays each initial password once.
- Server photos remain request-scoped. The originating browser keeps draft photos under
  the limits and lifecycle in decision 0008; there is no server photo store.
- GPT-5.5 with medium-quality JPEG inputs is the known-good generation baseline.
- The accepted application stack is Python 3.13 with `uv`, FastAPI/Uvicorn, a static
  HTML/CSS/vanilla JavaScript UI, Pydantic contracts, and Pillow plus `pillow-heif`.

## Completion report

Report:

1. The requested result.
2. Durable artifacts created or updated.
3. Validation performed and results.
4. Commit hash and subject.
5. Remaining unverified assumptions or blockers.
6. Any artifact deprecated or consolidated.
7. Unexpected failures, verified root causes, and durable prevention added during the
   task.

If nothing warranted promotion, state: `No durable knowledge promotion needed.`
