# Category selection before generation

The five task files decompose the user's category-menu story, including the two
added criteria for valid item-specific names and values. The implementation is complete; the task definitions remain the acceptance record.

[story-tasks.yaml](story-tasks.yaml) contains the complete output required by
[the task generator](../../prompts/task-generator.md): normalized story clauses,
ordered tasks, and coverage. Each `task-*.md` file contains the corresponding task
as plain YAML, without a Markdown code fence. Use that file as `TASK` and
`story-tasks.yaml` as both `STORY` and `STORY_TASKS` for the context compiler.

The aggregate file owns the task definitions. After a task definition changes,
update its individual file to match. The task files contain no repository context;
the context compiler supplies that context when each task is ready to start.

## Filename rule

Use the task's integer sequence number without zero padding. To form the slug,
convert its title to lowercase, replace each consecutive group of characters
outside `a-z` and `0-9` with one hyphen, and remove leading or trailing hyphens.
Use `task-{sequence_number}-{slug}.md` for every task.

## Tasks and dependencies

| Task | Outcome | Depends on |
| --- | --- | --- |
| [T1](task-1-reuse-stored-group-categories.md) | Reuse stored group categories | None |
| [T2](task-2-select-categories-from-group-menus.md) | Select categories from group menus | T1 |
| [T3](task-3-restore-saved-category-selections.md) | Restore saved category selections | T2 |
| [T4](task-4-generate-valid-category-specifics.md) | Generate valid category specifics | T2 |
| [T5](task-5-require-generation-after-category-changes.md) | Require generation after category changes | T3, T4 |

## Assumptions and boundaries

ASM-1 preserves the user's no-change assumption and marks it as unverified. It is
not a claim that eBay guarantees a fixed taxonomy. The tasks do not add scheduled
category refresh or replace existing checks with permanently stored aspect rules.

The goal to avoid missing specifics does not authorize invented facts. As agreed
in the earlier generation requirements, required facts absent from photos and
notes remain identified for seller input.

Each task owns its behavior checks. Use fake external responses and do not spend
OpenAI credits. Follow the project validation and delivery rules when implementing
each task. Each task was context-compiled before implementation with the deterministic candidate
tool and the context-compiler prompt. The compiled source packages are task-local
working files, not a second source of project truth.

## Completion evidence

| Task | Executable evidence |
| --- | --- |
| T1 | `tests/test_category_groups.py`; category-list PostgreSQL round trip in `tests/test_category_invalidation.py` |
| T2 | Browser group-menu layout, membership, retry, and delayed-response tests |
| T3 | Browser per-draft group restoration and delayed prior-draft load tests |
| T4 | `tests/test_category_generation.py`, including all group/type pairs and invalid-value boundaries |
| T5 | `tests/test_category_invalidation.py` and the browser category-change, reopen, failure, retry, and late-autosave flows |

Run `bin/validate-repo` and `uv run pytest -m browser`. To include the disposable
PostgreSQL checks, set `POSTGRES_TEST_BIN` to the directory containing `initdb` and
`pg_ctl`. These tests create and remove their own local database; they do not use the
operator database or live eBay/OpenAI services.
