"""Boundaries and packaging invariants for what S-Cockpit ships.

The pre-split repository checked every distribution at once, from paths no
published repository has, so none of this shipped. These are source scans
rather than integration tests, so the Cockpit can hold its own share and
fail in the pull request that breaks it.

The A5 assertions matter most here. The Cockpit aggregates other
applications' topics, which is precisely the shape that tends to acquire a
hard dependency on one of them by accident.
"""

import ast
import importlib.metadata
import unittest
from importlib.resources import files
from pathlib import Path

import s_cockpit
import sovereign


ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "src").rglob("*.py"))
OTHER_APPLICATIONS = ("s_team", "s_flow", "s_initiative")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }


class PackagingTests(unittest.TestCase):
    def test_distribution_and_module_versions_agree(self):
        self.assertEqual(
            importlib.metadata.version("sovereign-cockpit"),
            s_cockpit.__version__,
        )

    def test_installed_browser_assets_are_available(self):
        assets = files("s_cockpit.assets")
        self.assertTrue(assets.joinpath("boardofboards.html").is_file())
        self.assertTrue(assets.joinpath("boardofboards.css").is_file())

    def test_package_sources_live_under_the_declared_src_root(self):
        # Asserting where the imported module loaded from only holds for an
        # editable install: CI installs a wheel, so __file__ points into
        # site-packages. The invariant is this repository's layout - the
        # source sits under src/, and no flat copy survives beside it for an
        # import to pick up ahead of the installed package.
        self.assertTrue((ROOT / "src" / "s_cockpit" / "__init__.py").is_file())
        self.assertFalse((ROOT / "s_cockpit").exists())

    def test_live_context_does_not_nest_transport_under_session_snapshot(self):
        controller = (
            ROOT / "src" / "s_cockpit" / "controller.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "_composite_response(runtime, logic, logic.tiles_snapshot)",
            controller,
        )
        self.assertIn(
            "_composite_response(runtime, logic, logic.context_snapshot)",
            controller,
        )

    def test_distribution_has_no_kanban_dependency(self):
        # A5: S-Initiative is an optional, late-bound producer. The moment it
        # appears in `dependencies`, installing the Cockpit drags it in and
        # the optionality the architecture rests on is gone.
        metadata = importlib.metadata.metadata("sovereign-cockpit")
        required = [
            item for item in (metadata.get_all("Requires-Dist") or [])
            # Extras carry a marker; only unconditional requirements bind.
            if "extra ==" not in item
        ]
        self.assertTrue(required, "expected at least the Core dependency")
        for item in required:
            self.assertNotIn("s-initiative", item.lower())
            self.assertNotIn("s_initiative", item.lower())


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCES, "no S-Cockpit sources found")

    def test_imports_core_only_through_its_public_root(self):
        public_names = set(sovereign.__all__)
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    violations.extend(
                        alias.name for alias in node.names
                        if alias.name == "sovereign"
                        or alias.name.startswith("sovereign.")
                    )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith("sovereign."):
                        violations.append(module)
                    elif module == "sovereign":
                        violations.extend(
                            f"sovereign.{alias.name}"
                            for alias in node.names
                            if alias.name == "*" or alias.name not in public_names
                        )
            self.assertEqual(violations, [], str(path))

    def test_never_imports_another_application(self):
        # A5 makes S-Initiative an optional, late-bound producer. The Cockpit
        # reaches it through Core's host by application id, which is why it
        # starts with reduced function when S-Initiative is absent. An import -
        # even a guarded one inside a function - binds the two at load time.
        for path in SOURCES:
            imports = imported_modules(path)
            self.assertFalse(any(
                name == package or name.startswith(f"{package}.")
                for name in imports
                for package in OTHER_APPLICATIONS
            ), str(path))

    def test_only_the_desktop_entry_names_another_application(self):
        # Naming a producer at all is a step towards depending on it, so the
        # rule stays absolute everywhere it can. The desktop entry is the one
        # exemption: a frozen build has to list the packages to collect, and
        # the host mounts applications by module name. It earns the exemption
        # by probing rather than importing - see the guard test below.
        for path in SOURCES:
            if path.name == "desktop.py":
                continue
            source = path.read_text(encoding="utf-8")
            for package in OTHER_APPLICATIONS:
                self.assertNotIn(package, source, str(path))

    def test_the_desktop_entry_probes_for_producers_instead_of_importing_them(self):
        source = (ROOT / "src" / "s_cockpit" / "desktop.py").read_text(
            encoding="utf-8",
        )

        # The whole exemption rests on this: presence is discovered, so an
        # absent producer is skipped rather than raising at start-up.
        self.assertIn("find_spec", source)
        for package in OTHER_APPLICATIONS:
            self.assertNotIn(f"import {package}", source)
            self.assertNotIn(f"from {package}", source)

    def test_does_not_read_private_channel_services_from_config(self):
        for path in SOURCES:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn('config.get("_channel_manager")', source, str(path))
            self.assertNotIn('config.get("_relay_manager")', source, str(path))
            self.assertNotIn("channel_manager", source, str(path))

    def test_does_not_read_mutable_session_registries(self):
        forbidden = {
            "peer_topic_sets", "peer_perspectives", "peer_identity_key",
            "active_topic_uuids", "app_metadata",
        }
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            used = {
                node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
            }
            self.assertFalse(used & forbidden, str(path))

    def test_reads_the_transition_ranking_rather_than_copying_it(self):
        for path in SOURCES:
            source = path.read_text(encoding="utf-8")
            if "TRANSITION_PRIORITY" not in source:
                continue
            self.assertIn("Session.TRANSITION_PRIORITY", source, str(path))
            for literal in ('"divergence": 5', '"divergence": 6'):
                self.assertNotIn(literal, source, f"{path} re-declares the ranking")

    def test_domain_logic_does_not_depend_on_host_or_http_controllers(self):
        path = ROOT / "src" / "s_cockpit" / "logic.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = imported_modules(path)
        self.assertFalse(
            any(
                name == "starlette"
                or name.startswith("starlette.")
                or name.endswith(".controller")
                or name.endswith("_controller")
                or name == "sovereign.application"
                for name in imports
            ),
            str(path),
        )
        self.assertFalse(any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"build_routes", "create_application"}
            for node in tree.body
        ), str(path))
        self.assertFalse(any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(arg.arg == "runtime" for arg in node.args.args)
            for node in ast.walk(tree)
        ), str(path))


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.cockpit = files("s_cockpit.assets").joinpath(
            "boardofboards.html",
        ).read_text(encoding="utf-8")

    def test_assets_never_navigate_to_the_bare_root_with_a_query(self):
        # "/" serves whichever application is primary, so a root-relative
        # link lands somewhere that depends on host configuration. The board
        # link used "/?board=", which navigated back into the Cockpit
        # whenever the Cockpit was primary.
        for number, line in enumerate(self.cockpit.splitlines(), start=1):
            for pattern in ('href = `/?', 'href="/?', "href='/?"):
                self.assertNotIn(pattern, line, f"boardofboards.html:{number}")

    def test_people_and_multi_type_add_use_the_shared_ui_primitives(self):
        self.assertIn("SovereignUI.avatar", self.cockpit)
        self.assertIn('class="ui-button"', self.cockpit)
        self.assertIn("+ Add new…", self.cockpit)

    def test_cross_application_links_name_the_target_asset_prefix(self):
        self.assertIn("/apps/initiative?board=", self.cockpit)
        self.assertIn("/apps/flow?process_uuid=", self.cockpit)

    def test_flow_tiles_have_template_creation_and_owner_deletion_controls(self):
        self.assertIn('id="newFlowModal"', self.cockpit)
        self.assertIn('id="newFlowName"', self.cockpit)
        self.assertIn('id="newFlowTemplate"', self.cockpit)
        self.assertIn("state.flow_templates", self.cockpit)
        self.assertIn("/api/cockpit/flow/processes/create", self.cockpit)
        self.assertIn("/api/cockpit/flow/processes/delete", self.cockpit)
        self.assertIn("/api/cockpit/flow/processes/leave", self.cockpit)
        self.assertIn("process.can_delete", self.cockpit)

    def test_assets_do_not_call_producer_controller_namespaces(self):
        self.assertNotIn("/api/kanban", self.cockpit)
        self.assertNotIn("/api/team", self.cockpit)
        self.assertNotIn("/api/flow", self.cockpit)

    def test_cockpit_renders_one_application_identified_tile_stream(self):
        self.assertIn("tilesInDisplayOrder()", self.cockpit)
        self.assertIn("APP_ICONS", self.cockpit)
        self.assertIn("bob-topic-icon", self.cockpit)
        self.assertIn("/api/cockpit/tiles/reorder", self.cockpit)
        self.assertNotIn("expandedBoards()", self.cockpit)
        self.assertNotIn("collapsedBoards()", self.cockpit)

    def test_active_and_next_counts_live_on_the_expanded_bands(self):
        self.assertIn('count.className = "bob-band-count"', self.cockpit)
        self.assertIn("heading.append(title, count, sources)", self.cockpit)
        self.assertNotIn("involving you", self.cockpit)
        status_start = self.cockpit.index("function boardStatus(board)")
        status_end = self.cockpit.index("function statItem", status_start)
        status_source = self.cockpit[status_start:status_end]
        self.assertNotIn("active_cards", status_source)
        self.assertNotIn("next_cards", status_source)

    def test_cockpit_uses_the_shared_optimistic_session_view(self):
        self.assertIn("/api/cockpit/tiles", self.cockpit)
        self.assertIn("/api/cockpit/context", self.cockpit)
        self.assertIn('<script src="/shared-session.js"></script>', self.cockpit)
        self.assertIn("SovereignSessionView.create", self.cockpit)
        self.assertIn("sessionView.mutate", self.cockpit)
        self.assertIn("sessionView.startPolling", self.cockpit)
        self.assertIn("mutation_id: mutationId", self.cockpit)
        self.assertNotIn("queueMutation", self.cockpit)
        self.assertNotIn("pendingMutations", self.cockpit)
        self.assertNotIn("optimisticVersions", self.cockpit)
        self.assertNotIn(
            'api("/api/cockpit/summary")', self.cockpit,
        )


if __name__ == "__main__":
    unittest.main()
