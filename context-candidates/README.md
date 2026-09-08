# context-candidates

Deterministic repository candidate generation for task-context compilation.

The tool does **not** decide what context is ultimately required and does **not** implement the task. It constrains the repository search space for a later LLM selection pass.

## Requirements

- Ruby 3.1+
- Git
- No gems

## Usage

From the repository root:

```bash
./context-candidates/context-candidates \
  --repo /path/to/repo \
  --task /path/to/task.yaml \
  --story /path/to/story.yaml \
  --story-tasks /path/to/all-story-tasks.yaml \
  > candidates.json
```

`--task`, `--story`, and `--story-tasks` accept either a file path or inline text.

Useful limits:

```bash
--max-files 20
--max-total-files 80
--max-matches-per-file 8
--snippet-context 5
--max-snippet-lines 80
```

## Candidate discovery sequence

1. Normalize task/story text into search terms and short phrases.
2. Search only Git-tracked working-tree files with `git grep`.
3. Rank directly matching production source as `primary`.
4. Find related tests by basename, path terms, and content hits.
5. Find relevant schema/routes/contracts.
6. Load applicable `AGENTS.md` and `CLAUDE.md` files for every retained candidate, including tests and dependencies.
7. Search ADR/decision directories for matching task concepts.
8. Resolve one-hop explicit imports/requires for supported languages.
9. Optionally add candidates suggested by prerequisite sibling tasks.
10. Deduplicate by category precedence and enforce hard file caps.

## Output

JSON containing:

- repository revision metadata;
- normalized task/story requirements;
- deterministic terms and phrases used for retrieval;
- candidate categories;
- evidence showing why each file matched;
- bounded line excerpts around matches.

The intended next stage is an LLM that labels these candidates `REQUIRED`, `SUPPORTING`, `REFERENCE`, or `IRRELEVANT` and assembles the final context package.

## Notes

This intentionally does not do semantic search. The retrieval pass should be cheap, inspectable, repeatable, and easy to debug. Add language-aware indexing later only where exact/path/import retrieval demonstrably misses important files.

Structured story input preserves the complete text of each clause declared in
`story_clauses`. Other clause mentions do not add story requirements.
YAML date and timestamp scalars are accepted without enabling arbitrary object types.
Prerequisite discovery reads only the task's `dependencies` field and the matching
sibling task records. References in `non_goals` do not create dependencies.
Prerequisite discovery uses content matches and excludes task and story artifacts.
Those matches retain their evidence and source-line locations in the output.

HTML and CSS are production source. Files with a `test_` basename prefix are tests.
Ruby `require_relative "helper"` resolves relative to its source file. The match
limit applies to both evidence and the matches used to build excerpts.
Python imports with repository package names resolve to tracked modules or packages.
Imported Python submodules and JavaScript imports with explicit extensions also resolve.
Multiline JavaScript imports and comma-separated Python modules are included.
Dependency discovery scans the primary files retained after output limits apply.
Dependencies keep retained-primary rank and source import order when limits apply.
JavaScript dependency discovery skips comments and literal examples.
Literal dynamic imports are included; computed module names are ignored.
Python import discovery masks comments and string literals before reading declarations.
Go imports resolve relative to the module path declared in tracked `go.mod`.
Go discovery skips comments and literal examples, and supports grouped imports and aliases.
Search terms use structured values; field names do not become search terms.

Retained candidates include their scoped instructions. A candidate is skipped if
its required instructions cannot fit within the configured file limits.
Lower-ranked candidates are then considered until the retained category limit is reached.

Candidate reads exclude symbolic links and files below linked directories. Resolved
paths must stay inside the repository, so a tracked link cannot expose outside data.
Binary or invalid UTF-8 files do not contribute text excerpts.
Emitted source lines are limited to 1,000 characters, including truncation markers.
For a matching line, the excerpt is centered on the match when possible.

Run the deterministic CLI checks from the repository root:

```sh
ruby context-candidates/test_context_candidates.rb
```
