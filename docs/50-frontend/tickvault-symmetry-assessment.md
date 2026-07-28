# TickVault Frontend Symmetry Assessment

## Design intent

TickVault remains a dense, dark operator workstation. Symmetry means consistent
outer edges, gutters, baselines, paired surfaces, and responsive rhythm; it does
not mean giving operationally unequal information equal emphasis.

Design dials for this pass: visual variance 2/10, motion 3/10, density 8/10.

## Findings

| Area | Assessment | Resolution |
| --- | --- | --- |
| Application shell | The navigation uses a 2680px maximum at every desktop size while the page shell progresses through 1600px, 1800px, 2200px, and 2680px. Chrome and content edges therefore diverge from 1920px through 2999px. | Use one progressive shell-width rule for navigation and page content. |
| Page rhythm | Home uses different vertical spacing and Downloader narrows its whole page, including its header. Other route headers share the page-shell edge. | Add a shared page frame. Keep Downloader content readable, but align its header with every other route. |
| Page headers | Action clusters align differently when descriptions wrap and move to an unbalanced left edge on narrow screens. | Give copy and actions explicit grid areas and align actions to the trailing edge. |
| Home | The 8/4 bento makes the launchpad visually left-heavy even though Capture Monitor should remain primary. | Make Capture Monitor a full-width primary launch surface followed by three equal destination cards on desktop. |
| Monitor alerts | One alert fills half of a two-column grid; odd alert counts leave the last row visually incomplete. | Let the final alert span both columns when the count is odd. |
| Monitor panel pairs | The intentional 8/4 split is correct, but panels in each pair can end on different baselines because only their wrappers stretch. | Preserve the 8/4 hierarchy and stretch the paired panels to a shared row edge. |
| Option Chain and Stocks controls | Stocks has a complete toolbar while Option Chain collapses to a thin empty strip before symbols arrive. | Use a shared toolbar surface and show an explicit waiting label in the empty option state. |
| Option matrix and summary | A separate marker column displaces the semantic strike center, and seven header statistics form an unbalanced final row on narrow screens. | Merge markers into the sticky strike identity, then use paired mobile summary columns with the final statistic centered across the row and seven equal desktop columns. |
| Downloader | The entire route, including its page header, is capped at 1024px while all other route headers use the global shell. | Keep the header on the shared frame and center only the automation content column. |
| Dense tables and disclosures | Calls/strike/puts, stock legs, sticky identity columns, and mobile disclosure groupings are internally balanced and encode useful semantics. | Preserve their structure and data availability. |

## Acceptance invariants

- Navigation and main content share left and right edges at 1920px, 2400px, and
  3200px.
- Every route uses the same page-header origin and vertical stack rhythm.
- Home has a centered full-width primary card and three equal desktop destination cards.
- A single or final odd monitor alert fills its row.
- Both intentional 8/4 monitor pairs share top and bottom baselines.
- Option Chain and Stocks expose equally substantial control rows in empty states.
- No route introduces document-level horizontal overflow at supported widths.
- Routes, APIs, WebSocket topics, payloads, environment variables, data fields,
  focus behavior, and the standalone fallback monitor remain unchanged.
