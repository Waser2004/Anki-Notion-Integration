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
tree. A bounded asynchronous page queue first retrieves each selected page's
metadata, enhanced Markdown, and shallow root blocks. The queue uses fixed
workers, the shared Notion rate limiter and retry handling, and `queue.join()` to
wait for preparation to complete. Anki and database reconciliation then remains
sequential because those local APIs are not worker-thread safe:

The Anki progress dialog reports these as two text-only phases. `Fetching page
data` advances whenever a concurrent page job finishes, followed by `Parsing
page data` as each prepared page is reconciled sequentially.

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

Advanced cloze toggles are reconciled by the root-toggle pass and are excluded
from stale top-level-paragraph cleanup. If a mapped Anki note is missing, the
toggle is expanded and recreated even when its Markdown source hash is unchanged.
Parser revision refreshes also expand root toggles once so mappings detached by
an earlier parser or reconciliation bug can be recovered.

The marker palette from the last successful cloze sync is stored in the internal
settings row `_internal_cloze_marker_colors`. A changed palette forces cloze
re-rendering even when Notion timestamps are unchanged. After each updated cloze
note, sync removes only the Anki card instances whose cloze ordinals are now empty;
surviving cards and their scheduling data remain untouched.

- [Page object](https://developers.notion.com/reference/page)
- [Block object](https://developers.notion.com/reference/block)
- [Retrieve block children](https://developers.notion.com/reference/get-block-children)
- [Retrieve a page as Markdown](https://developers.notion.com/reference/retrieve-page-markdown)
- [Enhanced Markdown format](https://developers.notion.com/guides/data-apis/enhanced-markdown)

## Warnings

`SyncResult.warnings` contains structured `SyncWarning` entries with a stable code,
message, page id, and optional block id. Parsing failures, invalid cloze payloads,
empty toggles, stale sources, recoverable missing-note recreation, and usable media
fallbacks are warnings rather than sync errors.

Warnings do not prevent page edit timestamps or completed parser-refresh revisions
from being stored. A confirmed stale source detaches its Noteck mapping and override
but never deletes the existing Anki note. Unexpected parser exceptions preserve the
mapping so a future parser revision can retry it.

## Errors

`SyncResult.errors` is reserved for infrastructure failures in synchronization:
source access, required local configuration, database persistence, Anki collection
write operations, or unexpected orchestration failures. A failed page does not stop
later pages, but its edit timestamp is not advanced and the final result has
`ok=False`.
