# Changelog

## Unreleased

- **"+ Add new…" came out of the shell's bar.** The bar holds nothing of an
  application's now, so the button sits above the tiles it adds to. The
  navigation row the shell draws under a topic name is deliberately absent
  here: the Cockpit already shows every topic you hold, so a line offering a
  few of them — and offering the Cockpit itself — would be the second
  navigation mesh Core has refused since U3. Auto-adopt wording is inherited
  from Core rather than copied. See `DESIGN_UI_CONSISTENCY.md` U7.

- **Eight create routes became one, and six create methods became none.**
  `/api/cockpit/topics/create` makes a topic of any kind through Core's
  registry; the per-application create, copy and from-snapshot routes and
  their logic methods are gone, along with the list of nouns and the flow
  template lookup — `session.topic_kinds()` answers all three. Adding a
  fourth topic-creating application needs no change here at all.

- **Tiles ask the shell where another application's topic is.**
  `SovereignShell.topicHref(applicationId, uuid)` replaces three hardcoded
  routes, so an application deactivated on this host loses its links instead
  of keeping ones that go nowhere.

- **Three new-item dialogs became one.** New Initiative, New Organization and
  New Flow were the same form three times, differing in the noun and in
  whether a template was required. They are three calls to the shell's
  `openNewTopicDialog` now, each passing its noun, what there is to start
  from, and the create call. Nothing changes about what any of them makes.

- **"Stop holding it" beside Delete**, on an initiative and on a team. The
  Cockpit holds everything this client has, so this is where "I do not want
  this here any more" belongs, and it is not a delete: nothing is published,
  everybody else keeps theirs, and a peer who still publishes it will offer it
  back. Core refuses while anything here still references the topic and says
  how many places do. Removing a reference is not offered here and does not
  belong here — the Cockpit holds no links, so a reference comes off where it
  was made.

- Agenda counts and Collaboration panes now use Core's verified perspective
  projection while mutations continue to target locally authored items only.
  The staleness window is Core's default rather than a Cockpit declaration;
  the unused `agenda_perspective_*` configuration keys are gone.

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
