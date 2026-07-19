# Sync outcomes (`src/Noteck/modules/sync.py`)

Noteck reports four distinct outcomes:

- **Success:** all requested pages completed without warnings.
- **Success with warnings:** sync completed, but one or more cards were skipped,
  detached, repaired, or used a media fallback. `SyncResult.ok` remains `True`.
- **Sync error:** a Notion fetch, local configuration, database transaction, Anki
  write, or orchestration operation prevented requested content from syncing.
- **Canceled:** the user stopped the run. `cancelled=True`, `ok=False`, and
  `errors` remains empty.

## Change detection

Notion's page and block objects each expose their own `last_edited_time`. Noteck
does not treat either timestamp as a revision for the object's entire descendant
tree. Instead, every selected page is retrieved once as enhanced Markdown and
once as shallow root blocks:

1. Expiring signature parameters are removed from media URLs before hashing.
2. The cleaned full-page hash is stored in `pages.content_hash`.
3. Top-level regular-toggle Markdown sources are aligned by order with shallow
   Notion `toggle` blocks, which provide stable block IDs.
4. Each successfully handled toggle source hash is stored separately.
5. Only new, changed, repair-required, or parser-refresh toggles have their
   descendants fetched recursively.

If the Markdown response is truncated, malformed, or cannot be aligned
unambiguously with the shallow root toggles, Noteck retrieves the complete block
tree for that page. This preserves correctness instead of guessing identities.

Top-level cloze paragraphs are already complete in the shallow block response,
so they do not require descendant retrieval. Parsed payload hashes still decide
whether an Anki note needs to be created or updated.

- [Page object](https://developers.notion.com/reference/page)
- [Block object](https://developers.notion.com/reference/block)
- [Retrieve block children](https://developers.notion.com/reference/get-block-children)
- [Retrieve a page as Markdown](https://developers.notion.com/reference/retrieve-page-markdown)
- [Enhanced Markdown format](https://developers.notion.com/guides/data-apis/enhanced-markdown)

## Warnings

`SyncResult.warnings` contains structured `SyncWarning` entries with a stable code,
message, page id, and optional block id. Parsing failures, empty toggles, stale
sources, recoverable missing-note recreation, and usable media fallbacks are
warnings rather than sync errors.

Warnings do not prevent page edit timestamps or completed parser-refresh revisions
from being stored. A confirmed stale source detaches its Noteck mapping and override
but never deletes the existing Anki note. Unexpected parser exceptions preserve the
mapping so a future parser revision can retry it.

## Errors

`SyncResult.errors` is reserved for failures in synchronization itself: source
access, required local configuration, database persistence, Anki note operations,
or unexpected orchestration failures. A failed page does not stop later pages, but
its edit timestamp is not advanced and the final result has `ok=False`.
