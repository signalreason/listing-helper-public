id: T3
title: Restore saved category selections
goal: >
  Restore the selected category and its group when the user returns to a draft.
story_clauses: [BEH-1, BEH-2, ASM-1, ASM-2, TECH-1, TECH-2, AC-4]
required_behavior:
  - Preserve the selected category and enough group information with the draft to restore both menus.
  - When a draft opens, restore its group, populate that group's categories, and select the category saved for that draft.
  - Keep selections separate for different drafts so opening one draft does not apply another draft's category.
  - Use the stored-category behavior from T1 during restoration, including when the category list is not yet available locally.
dependencies: [T2]
non_goals:
  - Retrieval and storage of shared category lists remain owned by T1.
  - Do not regenerate a listing when restoring its saved selection.
  - Persisting and restoring category-change invalidation belongs to T5.
done_when:
  - Saving and reopening a draft restores the same category and matching group in the menus.
  - Opening two drafts with different selections restores each draft's own selection.
  - Restoration succeeds with stored categories and after a required category-list retrieval.
  - An earlier draft's delayed category load cannot replace the selection of the draft now open.
