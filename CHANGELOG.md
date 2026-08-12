# Changelog

## Unreleased

- Agenda counts and Collaboration panes now use Core's verified perspective
  projection while mutations continue to target locally authored items only.

- Root S-Team topics are presented as **Organizations** while nested topics
  remain Teams. The distinction comes from S-Team's facade projection; the
  Cockpit stores no second topic or Actor kind.
- Creating a Flow now opens a modal for its name and one of S-Flow's bundled
  templates. A Flow tile's settings let its creator delete its local copy or
  an invitee leave it locally, after confirmation.
- Reactions now use Core's shared control, replacing the Cockpit's copy of the
  reaction menu, its markup and its stylesheet. A divergence with one available
  act is a button naming it.
- Divergences can be answered from the collaboration pane for a board; a team's
  or a process's stay listed here and are answered in that application, which
  is where the Cockpit has routes to honour them.

## 0.1.0a3 - 2026-08-01

- Renamed from Personal Cockpit to **S-Cockpit**, distributed as
  `sovereign-cockpit`. The application id is now `cockpit`, routes are served
  under `/api/cockpit/`, and the Python package is `s_cockpit`. Its source
  adapters follow the applications they read: `initiative`, `team` and `flow`.
- **Fixed: one test mounted whatever the working directory happened to
  configure.** `load_config(None, "boardofboards")` searches the current
  directory for `boardofboards_config.json`, so running the suite from the
  repository root pulled in S-Team and S-Flow — applications this package does
  not depend on and CI does not install. The test now builds its config from
  the alias, which is what it was about.
- The Cockpit name shown in the shared header can be customized with
  `header_title` in the JSON configuration, including in browser clients.

## 0.1.0a2 - 2026-07-30

- Add GitHub Actions builds for Apple Silicon and Intel macOS application
  bundles, including the native icon and bundle metadata.
- Require Sovereign Core 0.1.5 for composite responses and the optimistic
  Session view.
- Active/Next bands now show every card in their mapped columns, with cards
  involving the local user ordered first. The mapped column name is aligned
  separately on the right.
- **Fixed: saved Active/Next column choices no longer return to "(not set)".**
  Legacy board bindings are migrated once instead of overwriting current
  settings on every tile refresh, and confirmed settings now redraw
  immediately instead of waiting for another tile interaction.
- Standalone compatibility payload builders are observation-free while their
  Session transaction is held; live liveness is merged only afterward.
- Cockpit reads and mutations now open their own Session transaction
  rather than relying on the HTTP layer to hold the lock, so board and
  agreement settings stay correct when called from a facade or a test.
- Cockpit selection, enlargement and tile ordering now use Core's shared
  optimistic Session view. Confirmed snapshots stay separate from pending
  intentions, timed-out mutations reconcile by ID without flipping back, and
  tile data refreshes separately from collaboration details.
- Boards and agreements now share one tile stream, with application icons;
  enlarged tiles precede collapsed overview tiles. Active/next counts moved
  from the board toolbar to their enlarged bands.
- Agreement agenda items can now be reordered from the Cockpit, using the same
  drag interaction as Kanban agenda items.
- All producer mutations now cross versioned Kanban/Agreement facades through
  S-Cockpit-owned controller routes; the UI no longer calls producer
  HTTP namespaces.
- Local portfolio state now uses its Session application metadata namespace.
- Core retired the direct HTTP channel. No production change was needed here
  - the Cockpit reads perspectives and never routed anything itself - and a
  peer is now named by its publication identity (`relay:…`) rather than a URL
  wherever one is shown.
- **Fixed: boards off the right-hand edge could not be reached.** The board
  row was sized as `100vh` minus a guessed top-bar height, so it finished a
  scrollbar's width past the bottom of the window - taking its own horizontal
  scrollbar with it. Narrowing the window hid tiles with no way to scroll to
  them. The page is now one viewport tall, with the bar taking what it needs
  and the row taking the rest.
- Agreement tiles carry the same controls as board tiles: move, enlarge,
  share and settings, over a count of divergences and agenda items.
  Enlarging an agreement shows the whole document, read-only; enlarging a
  board still opens its bands.
- Agreements can be deleted, from the same gear icon that deletes a board.
- Creating an agreement leaves you in the Cockpit, as creating a board
  already did, instead of jumping into the new document.
- Board tiles show their agenda-item count beside the divergence count, and
  name divergences as such rather than as "discussion".
- Initial standalone S-Cockpit with optional S-Initiative facade adapter.
