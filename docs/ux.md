# Interaction design: a shared bill, not a database editor

The UI follows Don Norman's interaction principles with concrete behavior,
not just a visual theme.

| Principle | Implementation |
|---|---|
| Discoverability & signifiers | Clear primary actions: add a receipt, split it, confirm *my amount*. Labeled inputs and a selected-token sentence make the next action visible. |
| Natural mapping | Numbered image boxes match numbered list rows. A row's number jumps to its location on the photo. Color is reinforced by names, initials and checkmarks. |
| Feedback | Uploads show a busy state; token edits acknowledge saving; assigned costs, remaining items and each person's agreement update live. Offline live updates are distinguished from failed saves. |
| Conceptual model | Totals are labeled **receipt totals**, **assigned costs**, and **each person's share**. Agreement is not represented as a payment or a debt settlement. |
| Constraints & error prevention | A confirmed split must have every item assigned. Exact-photo duplicate uploads reuse the existing receipt; a busy upload button prevents repeated submissions. |
| Recovery | Token changes offer Undo. Removing a receipt requires a dialog and is reversible; items, tokens and confirmations are retained. Withdrawal of personal agreement is explicit. |
| Recognition rather than recall | Receipt previews, date, uploader, assignment progress and waiting names replace filenames and raw status codes. Larger list controls provide an alternative to small image tokens. |

## Duplicate photos

Detection compares the normalized stored image bytes within the same tab.
It is not perceptual matching: a differently cropped or recompressed photo
may not be recognized. Existing duplicates are both flagged, without changing
money or deleting anything automatically. A newer copy may contain the group's
assignments while an older copy is empty, so the UI does not recommend keeping
the earliest one by default.

## Progressive enhancement and accessibility

SSR forms handle uploading, membership, receipt removal and restoration.
Small JavaScript handlers add copy-link feedback, dialogs, live review fragments
and token interactions. Keyboard-visible focus, named toggle states, explicit
confirmation explanations and reduced-motion support are part of the design.
The receipt list controls provide larger targets than the overlay tokens.

## Checks

- `uv run pytest -q tests/test_ux.py`: duplicate handling, confirmation constraints,
  removal/restore, authorization and honest totals. Temporary DB and upload dir.
- `node --test tests/hit-test.test.cjs`: adjacent OCR boxes at multiple image sizes.
- `scripts/ux_browser.py`: two actual Firefox sessions verify live assignment,
  agreement, token Undo, duplicate upload and removal/restore. Uses an isolated
  temporary database; never the running server's receipts.
