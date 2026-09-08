id: T4
title: Generate valid category specifics
goal: >
  Produce item specifics whose names and values meet the selected eBay
  category's rules.
story_clauses: [BEH-2, BEH-3, ASM-1, ASM-2, AC-5, AC-6]
required_behavior:
  - Use the selected eBay category and its item-specific rules to control generation.
  - Include only item-specific names permitted for that category in the returned generated listing.
  - Enforce the applicable eBay value constraints, including allowed values, number of values, length, type, format, and dependencies on other specifics.
  - Validate generated output before returning it; omit invalid specifics and recheck dependent values so the retained result meets the category rules.
  - Supply required specifics when supported by the photos or notes; identify required facts that still need seller input instead of inventing values.
dependencies: [T2]
non_goals:
  - Category menus and category-list storage belong to T1 and T2.
  - Persisting selections belongs to T3; category-change invalidation belongs to T5.
  - Do not silently repair seller edits or invent unknown item facts to fill required specifics.
done_when:
  - Fake eBay and model responses show that the selected category's rules control generated specifics.
  - Valid single-value and multiple-value specifics survive validation.
  - Unknown names and invalid values, repeated entries, formats, cardinalities, lengths, and dependent values do not remain in returned generated specifics.
  - Required specifics supported by the supplied evidence are retained, while unknown required facts are identified without invented values.
  - Categories within the inventory scope in ASM-2 have coverage, and tests spend no OpenAI credits.
