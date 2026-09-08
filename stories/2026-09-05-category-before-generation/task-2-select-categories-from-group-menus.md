id: T2
title: Select categories from group menus
goal: >
  Let the user choose a category group and then its category from two
  pre-populated menus before listing generation.
story_clauses: [BEH-1, BEH-2, ASM-1, ASM-2, TECH-1, TECH-2, AC-1, AC-2]
required_behavior:
  - Show a category-group dropdown above Generate listing when the user visits the page.
  - Show a category dropdown next to the group dropdown and populate it from the selected group's categories supplied by T1.
  - Allow the user to choose a category without entering search text, and expose the selected category identity for generation and draft saving.
  - When the group changes, replace the category choices with those for that group and clear any selection that does not belong to it.
  - Keep an earlier group's delayed result from populating the menu after the user selects a different group.
dependencies: [T1]
non_goals:
  - Do not add a second category retrieval or storage mechanism; use the outcome of T1.
  - Saving and restoring draft selections belong to T3.
  - Generated-specific validation belongs to T4, and invalidation after generation belongs to T5.
done_when:
  - Browser tests show both menus in the required position on page entry.
  - Selecting each supported group shows only categories that belong to that group, without a text search.
  - Choosing a category exposes its eBay identity, and a group change cannot retain an unrelated category selection.
  - A delayed response for an earlier group cannot overwrite the current group's choices.
  - The two menus remain usable in phone and desktop layouts.
