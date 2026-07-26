# Improved page sync behaviour

## Goals

- Make page-selection rules explicit instead of hidden behind checkbox side effects.
- Let users decide whether parent page selections should include no children, only currently visible children, or child pages added in Notion after the parent was selected.
- Keep the existing behaviour as the default so current users are not surprised after upgrade.

## Where behavior is exposed

- **Pages tab:** shows the active selection behavior in the page tree status label.
- **Settings tab:** exposes the persisted Sync setting as `Pages tab selection behavior`.

## Settings

1. **Manual** (`manual`)
   - Checking a page affects only that page.
   - Parent pages and child pages stay independent.
   - Users can still use the context menu action `Select Page and All Children` for an explicit one-time subtree selection.
2. **Smart** (`existing_descendants`)
   - Keeps the previous default: checking a parent with no selected descendants selects the currently known subtree.
   - Children created in Notion later are not automatically selected unless the user selects them or reselects the subtree.
3. **Dynamic** (`dynamic_descendants`)
   - Treats each selected parent as a live subtree subscription.
   - When a Pages refresh discovers new Notion child pages below a selected parent, those children are selected and persisted automatically.
   - Descendants cannot be unselected independently while their selected parent remains an active subtree subscription.
   - `Select Page and All Children` establishes the same persistent subtree selection as checking the parent.

## Implementation

- Page-selection behavior constants, short labels, detailed mode descriptions, and option-specific tooltips live in `src/Noteck/modules/pages.py`.
- `apply_selection_rule()` runs manual, smart, or dynamic checkbox behavior.
- The behavior is persisted in the existing `settings` table under `page_selection_behavior`; no schema migration is required because settings are key/value rows.
- The Settings tab dropdown uses the short labels `Manual`, `Smart`, and `Dynamic`, shows an italic description for the selected mode, and uses concise behavior-specific tooltip text for option hover help.
- During incremental page loading, dynamic descendant selection is applied before persisting so newly discovered child pages under selected parents are synced.
- Dynamic subtree roots are persisted in settings so live subtree subscriptions survive Pages refreshes.
- Unit coverage validates manual selection, dynamic selection, fallback behavior, and shared labels/tooltips.
