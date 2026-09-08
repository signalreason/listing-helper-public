id: T1
title: Reuse stored group categories
goal: >
  Make each supported group's eBay categories available from local storage,
  with retrieval from eBay only when the list is absent.
story_clauses: [ASM-1, ASM-2, TECH-1, TECH-2]
required_behavior:
  - Provide category groups and their category relationships for the supported inventory in ASM-2.
  - Return the stored categories for a group without another eBay category-list request when those categories are available locally.
  - Retrieve a group's categories from eBay only when they are not available locally, then store the successful result for later use.
  - Preserve each category's identity and group relationship so selection and later generation refer to the same eBay category.
  - Keep failed retrievals distinct from successfully stored category lists so a later request can retry.
dependencies: []
non_goals:
  - Menu rendering and selection behavior belong to T2.
  - Draft selection persistence belongs to T3.
  - Item-specific generation and value validation belong to T4.
  - Scheduled taxonomy refresh or automatic category replacement is outside this story's stated assumption.
done_when:
  - Tests with fake eBay data show that a local miss retrieves and stores the requested group's categories.
  - A later request for that group uses the stored list and makes no eBay category-list request.
  - Lists for different groups retain the correct category identities and relationships, including the inventory scope in ASM-2.
  - A failed retrieval does not become a stored successful list, and a later request can succeed.
