# Fix plan: home-screen sanity misread (dark-panel theme reads 112 as 2)

## The bug, as seen on the real device

- On 2026-10-28 the account's home-screen sanity panel is the **dark theme**: dark grey panel, **white** digits, light-grey wireframe icon behind them, "理智/210" underneath.
- Real sanity was **112**, but `maapilot state sanity` returned **2**.
- The daily farm sized its time limit from that number: ceil(2/6)=1 fight → 1×3+15 = **18 min**. The farm timed out while MAA was still fighting, and the task was handed back as a failure.
- The same screen at sanity 4 read correctly as 4. That was luck: one wide glyph, nothing lost.

Evidence: two fixture images are already committed on the `pi-harness` branch in `tests/fixtures/screens/`. They were masked the same way as `tests/fixtures/screens/make.py --keep 640 15 640 265`: 1280×720, everything outside the home screen's top-right area is black, and there is no nickname or ID.
- `home_dark_112.png`: real 112, max 210, current code reads 2.
- `home_dark_4.png`: real 4, max 210, current code reads 4.

Use them as they are. The original screenshots are private and not available to you.

Pixel facts in `SANITY_NUM_ROI = (758, 128, 140, 98)`:

| image | ROI median RGB | px with max<100 ("dark") | px with min>220 ("light") |
|---|---|---|---|
| home_dark_112 (dark theme) | ~70 | 10098 (nearly the whole ROI) | 2021 |
| home_dark_4 (dark theme) | ~65 | 10995 | 910 |
| `tests/fixtures/screens/home_130.png` (light theme) | ~190 | 2197 | 2342 |

## Root cause (`maapilot/tools/state.py`)

1. `sanity_glyphs` (line ~147) assumes dark text on a light background: `dark = roi.max(axis=2) < SANITY_DARK`.
   - On the dark theme, the background itself is "dark", so the mask is one big blob.
   - The column-run segmentation then yields 0 runs, or a single run as wide as the ROI, so the function returns None.
2. Because `sanity_glyphs` returned None, `home_sanity` (line ~228) falls back to `parse_sanity(hits)` on the raw OCR.
   - Raw OCR mangles the "1"s in this font (it already did on the light theme: 110→0, 130→29…).
   - `_number` strips leading non-digit junk via `_EDGE_JUNK`, so "{{2"-like text becomes **2**: a confident wrong number instead of None.

## Fix

Work on the **`pi-harness` branch** (this plan and the fixtures live there): pull it, then commit the fix on top of it, or on a branch made from it.
If you work on the user's own machine, use a **separate git worktree**: the main checkout there is in use by a pi session that reads its docs from it.

### 1. Make `sanity_glyphs` polarity-aware (the real fix)

- Decide the polarity from the ROI background, for example the ROI median brightness (< ~128 means the dark theme).
  - **Dark theme:** use a light-glyph mask, `roi.min(axis=2) > T_LIGHT`.
    - **Measure `T_LIGHT` on `home_dark_112.png`.** The wireframe icon behind the digits is light grey and must fall below the threshold. Start near 220 and check that only the digit strokes survive.
    - If the icon still leaks in, also require low saturation and pick a threshold between the icon's grey and the digits' white.
  - **Light theme:** keep the existing dark mask unchanged.
- Build `clean` as **black-on-white from whichever mask was used**. The OCR step in `sanity_from_image` and the "1"-by-width logic (`ONE_RATIO`) then work unchanged.
- Re-check the other assumptions under the new mask:
  - glyph height ≥ 30;
  - drop a run touching the left edge (the "+" button's edge);
  - run width ≥ 4;
  - at most 3 runs.
- The "+" button is white on dark in this theme. Confirm it stays left of x=758, or is dropped by the left-edge rule.

### 2. Stop the fallback from inventing numbers (defence in depth)

- In `home_sanity`'s fallback path (`parse_sanity`, the "大号数字 + 理智/上限" branch), reject a candidate when `_EDGE_JUNK` had to strip characters that look like a misread "1".
  - Characters to treat that way include `{ [ ( I l | 亻 7`; check what the OCR actually produced on `home_dark_112.png` with `maapilot screen ocr --image tests/fixtures/screens/home_dark_112.png --roi 640 15 640 265`.
- Return None instead. `farm_timeout` then falls back to `task_timeout_min` (safe), rather than sizing from a wrong small number.
- Keep the "82/135" one-line format path as is.

### 3. Regression test (follow `CLAUDE_DEV.md` 「识别回归测试」)

1. The fixtures `home_dark_112.png` and `home_dark_4.png` are already in `tests/fixtures/screens/`.
2. `tests/test_screens.py::test_home_sanity` hard-codes max 205. Add a separate parametrized test, or extend the param to `(name, value, max)`, asserting `(112, 210)` and `(4, 210)`.
3. Run it, **see it fail first** (112 → 2 today), then fix.
4. All existing `HOME_SANITY` light-theme cases must still pass.
5. Add a unit test for step 2 with fake hits, e.g. text "{{2" above "理智/210" → `parse_sanity` returns None.
6. `tests/test_screens.py` is skipped when the OCR model (`resource/model/ocr/`) is missing. Confirm it actually ran rather than skipped.

### 4. Verify on the device (read-only)

- After the change, with the emulator on the home screen, run `maapilot state sanity` (read-only, needs no lock).
- Compare it with a screenshot (`maapilot screen shot`). Don't run a daily for this.
- Also check light theme if the account can still show it; otherwise rely on the existing fixtures.

## Out of scope (mention, don't do)

- **Farm watchdog that extends while MAA is still fighting.** `maapilot/flows/daily/farm.py`'s `farm_timeout` is purely sanity-based, so any misread still produces too short a limit. Worth a separate change.
- **`state box` only reads from the current scroll position.** It's a separate bug; pi worked around it by scrolling to the top first.
- **The user's private note** `private/memory/sanity-ocr-misread.md` says to check sanity against a screenshot. Leave it; it can be removed once the fix is merged and verified on the device.

## Done means

- Commits on `pi-harness` (or a branch from it) with the polarity-aware `sanity_glyphs`, the fallback guard and the new tests.
- Delete this plan file (`docs/plans/sanity-ocr-dark-theme.md`) in the same change once the fix is in.
- The full test suite passes, and the OCR screen tests actually ran.
- One read-only device check reports 3-digit sanity correctly.
- A commit (and a PR if asked) describing the dark-theme cause.
