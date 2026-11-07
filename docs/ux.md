# Interaction design: a bill calculator, not a database editor

The UI follows Don Norman's interaction principles with concrete behavior,
not just a visual theme.

| Principle | Implementation |
|---|---|
| Discoverability & signifiers | Clear primary actions: add a receipt, add who's splitting, tap what each had, tick people off. Labeled inputs and a selected-token sentence make the next action visible. |
| Natural mapping | Numbered image boxes match numbered list rows. A row's number jumps to its location on the photo. Color is reinforced by names, initials and checkmarks. |
| Feedback | Uploads show a busy state; token edits acknowledge saving; assigned costs, remaining items, people and ticks update live on every phone with the link. Offline live updates are distinguished from failed saves. |
| Conceptual model | One receipt, one link, one split. Totals are labeled **receipt totals**, **assigned costs**, and **each person's share**. A tick means "checked with them", not a payment or a debt settlement. |
| Constraints & error prevention | A tick records the share it was given at; if that person's share changes, the tick lapses (only theirs) and says what they agreed to. Tokens go only to people on the receipt, and names are unique on it. A busy upload button prevents repeated submissions. |
| Recovery | Token changes offer Undo; a tick is undone by tapping it again. Removing someone on items asks first and says what happens to their items; someone on nothing goes at once. |
| Recognition rather than recall | Each person's row lists what they had, to read out when asking them. Home lists this device's receipts with previews and progress. Larger list controls provide an alternative to small image tokens. |

## Progressive enhancement and accessibility

SSR forms handle uploading and the currency. Small JavaScript handlers add
sharing the link, dialogs, live review fragments, people and token interactions.
Keyboard-visible focus, named toggle states (each tick is a pressed/unpressed
button) and reduced-motion support are part of the design.
The receipt list controls provide larger targets than the overlay tokens.

## Checks

- `uv run pytest -q tests/test_ux.py`: link-only access, people, ticks that lapse
  with a changed share, this device's home list and honest totals. Temporary DB and upload dir.
- `node --test tests/hit-test.test.cjs`: adjacent OCR boxes at multiple image sizes.
- `scripts/ux_browser.py`: two actual Firefox sessions on one link verify adding people,
  live assignment, ticks, token Undo, repeat-tap token counts, removing a person and
  uploading from home. Uses an isolated
  temporary database; never the running server's receipts.
