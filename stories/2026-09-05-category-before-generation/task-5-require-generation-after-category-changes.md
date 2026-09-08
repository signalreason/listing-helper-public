id: T5
title: Require generation after category changes
goal: >
  Require an explicit new generation when the user changes the category of
  an already generated listing.
story_clauses: [BEH-2, BEH-3, ASM-1, ASM-2, AC-3, AC-4, AC-5, AC-6]
required_behavior:
  - Mark the generated listing invalid when its selected category changes, and make the need to generate again clear to the user.
  - Keep the invalid listing from being treated as ready for publication under the changed category.
  - Start generation for the new category only when the user presses Generate listing; a category change alone must not start it.
  - Preserve the selected category and invalid state when the draft is saved and reopened, and retain that state after failed generation.
  - Clear the invalid state only after successful generation for the changed category, using the valid-specific behavior from T4.
dependencies: [T3, T4]
non_goals:
  - Do not implement automatic regeneration or automatic repair of the previous listing's specifics.
  - Category-list storage, menu rendering, and selection restoration remain owned by T1 through T3.
  - Do not duplicate the item-specific validation owned by T4.
done_when:
  - A generated listing becomes invalid after a category change, with no model request until Generate listing is pressed.
  - Saving and reopening that draft retains the new category and still requires generation before publication.
  - A failed generation leaves the listing invalid and permits an explicit retry with the selected category.
  - Successful generation uses the new category, returns specifics that meet AC-5 and AC-6, and clears the invalid state.
  - An end-to-end test covers group selection, category selection, generation, category change, draft reopening, and explicit generation again.
