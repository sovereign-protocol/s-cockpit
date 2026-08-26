import tempfile
import unittest
from pathlib import Path

import app_server
from s_cockpit.logic import BoardOfBoardsLogic
try:
    from s_initiative.facade import InitiativeFacade
    from s_initiative.logic import InitiativeLogic
except ImportError:  # pragma: no cover - depends on what is installed
    InitiativeFacade = InitiativeLogic = None
from sovereign import ApplicationRegistration, SessionResult
from sovereign.protocol import ProtocolNode


# A5: S-Cockpit may depend on another application only optionally.
# Its own suite must therefore run with S-Initiative absent, which is also the
# only way CI can install it before S-Initiative exists on an index.
requires_initiative = unittest.skipIf(
    InitiativeLogic is None, "S-Initiative is not installed",
)


class _FacadeLookup:
    def __init__(self, initiative, team=None, flow=None):
        self.initiative = initiative
        self.team = team
        self.flow = flow

    def find(self, application_id, facade_api_version):
        if application_id == "initiative" and facade_api_version == 3:
            return self.initiative
        if application_id == "team" and facade_api_version == 3:
            return self.team
        if application_id == "flow" and facade_api_version == 2:
            return self.flow
        return None


class _StubTeamFacade:
    """S-Team's facade, over nodes this test makes itself.

    The Cockpit consumes an interface, not a package - S-Team is as
    optional as S-Initiative (A5) - so the Cockpit's own summaries are tested
    against the interface. That the real application implements it is
    S-Team's test to make.
    """

    def __init__(self, session):
        self.session = session
        self.uuids = []
        self.observed_networks = []
        self.holders = {}
        self.non_roots = set()

    def create(self, title):
        node = self.session.create_child(
            self.session.root_uuid(), {"type": "team", "title": title}, {},
        ).value
        self.uuids.append(node.uuid)
        return node.uuid

    def add_section(self, team_uuid, title, order=0):
        return self.session.create_child(
            team_uuid,
            {"type": "team_section", "title": title, "order": order},
            {},
        ).value.uuid

    def add_clause(self, section_uuid, text, order=0):
        return self.session.create_child(
            section_uuid,
            {"type": "team_clause", "text": text, "order": order},
            {},
        ).value.uuid

    def add_role(self, team_uuid, name, order=0):
        return self.session.create_child(
            team_uuid,
            {"type": "team_role", "name": name, "order": order},
            {},
        ).value.uuid

    def hold_role(self, role_uuid, name, status="accepted"):
        """Somebody offered this role, and where their answer stands."""
        self.holders.setdefault(role_uuid, []).append({
            "actor_uuid": f"actor-{name}",
            "name": name,
            "picture": "",
            "actor_kind": "individual",
            "is_self": False,
            "status": status,
        })

    def teams(self):
        nodes = [self.session.protocol.index.get(uuid) for uuid in self.uuids]
        return [node for node in nodes if node and not node.deleted]

    def sections(self, team):
        return self._ordered(team, "team_section")

    def clauses(self, section):
        return self._ordered(section, "team_clause")

    def roles(self, team):
        return self._ordered(team, "team_role")

    def is_organization(self, team):
        return team.uuid not in self.non_roots

    def role_holders(self, team, role):
        return list(self.holders.get(role.uuid, []))

    def participants(self, team_uuid):
        # Actors are who holds something here, and each is listed with what
        # they hold - an actor holding nothing accepted is an observer.
        team = self.session.protocol.index.get(team_uuid)
        people = {}
        for role in self.roles(team):
            for holder in self.role_holders(team, role):
                person = people.setdefault(
                    holder["actor_uuid"],
                    {**holder, "uuid": holder["actor_uuid"], "roles": []},
                )
                person["roles"].append({
                    "uuid": role.uuid,
                    "name": role.data.get("name", ""),
                    "status": holder["status"],
                })
        for person in people.values():
            person["is_observer"] = not any(
                item["status"] == "accepted" for item in person["roles"]
            )
        return list(people.values())

    @staticmethod
    def _ordered(parent, node_type):
        return sorted(
            [
                child for child in parent.live_children()
                if child.data.get("type") == node_type
            ],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    def transition_events(self, team_uuid, network=None):
        self.observed_networks.append(network)
        return []

    def collaboration_context(self, topic_uuid, network=None):
        self.observed_networks.append(network)
        return {
            "agenda_items": [
                item.to_dict()
                for item in self.session.agenda_items(topic_uuid)
            ],
            "transition_events": [],
            "transition_by_node": {},
            "identity_uuid": self.session.identity.uuid,
            "known_identities": self.session.known_identities(),
        }

    def create_agenda_item(self, team_uuid, text, priority=None):
        return self.session.create_agenda_item(
            team_uuid, text, priority,
        )

    def delete_agenda_item(self, item_uuid):
        return self.session.delete_agenda_item(item_uuid)

    def update_agenda_item(self, item_uuid, text):
        return self.session.update_agenda_item_text(item_uuid, text)

    def set_agenda_item_priority(self, item_uuid, priority):
        return self.session.set_agenda_item_priority(item_uuid, priority)

    def move_agenda_item(self, item_uuid, index):
        return self.session.move_agenda_item(item_uuid, index)


class _StubFlowFacade:
    def __init__(self, session):
        self.session = session
        self.uuids = []

    def create_process(
        self, title, definition_id="integrative-election",
        definition_version="0.2.0",
    ):
        result = self.session.create_child(
            self.session.root_uuid(),
            {
                "type": "flow_process",
                "title": title,
                "definition_id": definition_id,
                "definition_version": definition_version,
                "lifecycle": "setup",
                "current_stage": "Configure participants",
            },
            {},
        )
        if result.status == "ok":
            self.uuids.append(result.value.uuid)
            return type(result)(
                "ok", value=result.value.uuid, effects=result.effects,
            )
        return result

    @staticmethod
    def templates():
        return [
            {
                "id": "integrative-election",
                "version": "0.2.0",
                "name": "Integrative Election",
                "description": "Elect a candidate through nomination and consent.",
            },
            {
                "id": "minimal-consent",
                "version": "0.2.0",
                "name": "Minimal Consent Decision",
                "description": "Make a small decision by consent.",
            },
        ]

    def processes(self):
        nodes = [self.session.protocol.index.get(uuid) for uuid in self.uuids]
        return [node for node in nodes if node and not node.deleted]

    def process_summary(self, process):
        return {
            "uuid": process.uuid,
            "title": process.data["title"],
            "application_id": "flow",
            "definition_id": process.data["definition_id"],
            "definition_version": process.data["definition_version"],
            "lifecycle": process.data["lifecycle"],
            "last_completed": "",
            "current_stage": process.data["current_stage"],
            "required_from_me": "Configure participants",
            "assignment_count": 1,
            "agenda_count": len(self.session.agenda_items(process.uuid)),
            "content_hash": process.content_hash,
            "can_delete": True,
            "can_leave": False,
        }

    def collaboration_context(self, topic_uuid, network=None):
        return {
            "agenda_items": [
                item.to_dict()
                for item in self.session.agenda_items(topic_uuid)
            ],
            "transition_events": [],
            "transition_by_node": {},
            "identity_uuid": self.session.identity.uuid,
            "known_identities": self.session.known_identities(),
            "network": network or {},
        }

    def delete_process(self, process_uuid):
        return self.session.delete(process_uuid)

    def leave_process(self, process_uuid):
        return self.session.delete(process_uuid)

    def create_agenda_item(self, process_uuid, text, priority=None):
        return self.session.create_agenda_item(
            process_uuid, text, priority,
        )

    def delete_agenda_item(self, item_uuid):
        return self.session.delete_agenda_item(item_uuid)

    def update_agenda_item(self, item_uuid, text):
        return self.session.update_agenda_item_text(item_uuid, text)

    def set_agenda_item_priority(self, item_uuid, priority):
        return self.session.set_agenda_item_priority(item_uuid, priority)

    def move_agenda_item(self, item_uuid, index):
        return self.session.move_agenda_item(item_uuid, index)


def register_stub_application(session, application_id, root_type, noun, stub):
    """Stand in for a producer the way a real one stands: registered.

    Making a topic goes through Core's registry now, so a stub that only
    answered a facade lookup could be read from and not created in - which
    is not what an application absent from this host looks like.
    """
    def create_topic(title, template, snapshot):
        if application_id == "flow":
            chosen = next(
                (item for item in stub.templates()
                 if item["id"] == str(template or "")),
                None,
            )
            if not chosen:
                return SessionResult(
                    "error", reason="choose a workflow to start from",
                )
            return stub.create_process(title, chosen["id"], chosen["version"])
        return (
            stub.clone_team(template, title) if template
            else stub.create_team(title)
        )

    session.register_application(ApplicationRegistration(
        application_id,
        frozenset({root_type}),
        lambda: [],
        session.accept_topic_invitation,
        assignment_scoped=True,
        mount_invitation=True,
        topic_noun=noun,
        template_required=application_id == "flow",
        list_templates=lambda: (
            [
                {
                    "value": item["id"],
                    "name": item["name"],
                    "description": item.get("description", ""),
                }
                for item in stub.templates()
            ]
            if application_id == "flow" else []
        ),
        create_topic=create_topic,
    ))


def cockpit(runtime, team=None, flow=None):
    if team is not None:
        register_stub_application(
            runtime.session, "team", "team", "Organization", team,
        )
    if flow is not None:
        register_stub_application(
            runtime.session, "flow", "flow_process", "Flow", flow,
        )
    return BoardOfBoardsLogic(
        runtime.session,
        runtime.config,
        facades=_FacadeLookup(
            InitiativeFacade(runtime.logic), team, flow,
        ),
    )


class CockpitWithoutInitiativeTests(unittest.TestCase):
    """Runs whether or not S-Initiative is installed - that is the point."""

    def test_without_initiative_facade_is_empty_and_reports_source_unavailable(self):
        directory = tempfile.TemporaryDirectory()
        config = app_server.load_config()
        config.update({
            "applications": [{"module": "s_cockpit.application"}],
            "primary_application_id": "cockpit",
            "storage_file": str(Path(directory.name) / "cockpit-only.json"),
        })
        runtime = app_server.create_runtime(8499, config)
        runtime._test_tmp = directory
        bob = runtime.logic

        payload = bob.summary_payload()

        self.assertEqual(payload["initiatives"], [])
        self.assertFalse(payload["sources"]["initiative"]["available"])
        self.assertIn("not active", payload["sources"]["initiative"]["reason"])
        result = bob.reorder_initiatives([])
        self.assertEqual(result.status, "error")

@requires_initiative
class BoardOfBoardsLogicTests(unittest.TestCase):
    def test_compatibility_payload_uses_explicit_detached_observations(self):
        runtime = self.runtime(8534)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        team_uuid = team.create("No nested transport")
        bob.select_topic(team_uuid)

        bob.summary_payload()

        self.assertTrue(team.observed_networks)
        self.assertTrue(all(
            network == {} for network in team.observed_networks
        ))

    def test_application_host_supplies_live_initiative_facade(self):
        directory = tempfile.TemporaryDirectory()
        # Not load_config(): it searches the working directory for
        # `boardofboards_config.json`, so running this suite from the
        # repository root silently mounts every application that file lists -
        # including S-Team and S-Flow, which this package does not depend on
        # and CI does not install. The alias is what this test is about.
        config = dict(app_server.DEFAULT_CONFIG)
        config.update(app_server.app_default_config("boardofboards"))
        config["storage_file"] = str(Path(directory.name) / "cockpit.json")
        runtime = app_server.create_runtime(8498, config)
        runtime._test_tmp = directory
        initiative_logic = runtime.host.instances["initiative"].logic
        initiative_logic.ensure_initiative()

        self.assertEqual(runtime.host.primary_instance.manifest.application_id,
                         "cockpit")
        self.assertEqual(len(runtime.logic.summary_payload()["initiatives"]), 1)
        self.assertTrue(
            runtime.logic.summary_payload()["sources"]["initiative"]["available"],
        )

    def test_summary_lists_all_boards_collapsed_by_default(self):
        runtime = self.runtime(8501)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)

        board_a = initiative_logic.ensure_initiative()
        board_b_uuid = initiative_logic.create_initiative("Board B").value

        payload = bob.summary_payload()
        self.assertEqual({item["uuid"] for item in payload["initiatives"]}, {board_a.uuid, board_b_uuid})
        self.assertTrue(all(not item["expanded"] for item in payload["initiatives"]))

        bob.pick_initiative(board_a.uuid, [], [])
        payload = bob.summary_payload()
        expanded = [item for item in payload["initiatives"] if item["expanded"]]
        self.assertEqual([b["uuid"] for b in expanded], [board_a.uuid])

    def test_tiles_and_selected_collaboration_are_separate_payloads(self):
        runtime = self.runtime(8532)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        runtime.session.create_agenda_item(initiative.uuid, "Discuss timing")

        tiles = bob.tiles_payload()
        context = bob.context_payload()

        self.assertNotIn("agenda_items", tiles)
        self.assertEqual(context["selected_topic"]["uuid"], initiative.uuid)
        self.assertEqual(len(context["agenda_items"]), 1)
        self.assertIn("agenda_items", bob.summary_payload())

    def test_summary_carries_columns_and_settings_for_each_board(self):
        runtime = self.runtime(8511)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)

        collapsed = next(
            item for item in bob.summary_payload()["initiatives"] if item["uuid"] == initiative.uuid
        )
        self.assertFalse(collapsed["expanded"])
        self.assertEqual(
            {c["uuid"] for c in collapsed["columns"]},
            {todo.uuid, doing.uuid, done.uuid},
        )
        self.assertEqual(collapsed["active_column_uuids"], [])

        bob.pick_initiative(initiative.uuid, [doing.uuid], [todo.uuid])

        picked = next(
            item for item in bob.summary_payload()["initiatives"] if item["uuid"] == initiative.uuid
        )
        self.assertTrue(picked["expanded"])
        self.assertEqual(picked["active_column_uuids"], [doing.uuid])
        self.assertEqual(picked["next_column_uuids"], [todo.uuid])

    def test_active_and_next_bands_partition_by_mapped_columns(self):
        runtime = self.runtime(8502)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        my_id = initiative_logic.user_profile().uuid

        next_card = initiative_logic.create_card(todo.uuid, "Next task", "", [my_id]).value
        active_card = initiative_logic.create_card(doing.uuid, "Active task", "", [my_id]).value
        initiative_logic.create_card(done.uuid, "Done task", "", [my_id])

        bob.pick_initiative(initiative.uuid, [doing.uuid], [todo.uuid])

        summary = bob.summary_payload()["initiatives"][0]
        self.assertEqual([c["uuid"] for c in summary["active_cards"]], [active_card.uuid])
        self.assertEqual([c["uuid"] for c in summary["next_cards"]], [next_card.uuid])

    def test_active_band_includes_all_cards_with_personal_cards_first(self):
        runtime = self.runtime(8513)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        my_id = initiative_logic.user_profile().uuid

        mine = initiative_logic.create_card(doing.uuid, "Mine", "", [my_id]).value
        someone_elses = initiative_logic.create_card(
            doing.uuid, "Someone else's", "", ["other-user-id"],
        ).value
        unassigned = initiative_logic.create_card(doing.uuid, "Unassigned").value
        bob.pick_initiative(initiative.uuid, [doing.uuid], [])

        summary = bob.summary_payload()["initiatives"][0]

        self.assertEqual(
            [c["uuid"] for c in summary["active_cards"]],
            [mine.uuid, someone_elses.uuid, unassigned.uuid],
        )
        self.assertEqual(
            [c["relevance"] for c in summary["active_cards"]],
            ["participant", "other", "other"],
        )

    def test_owner_cards_sort_before_participant_cards(self):
        runtime = self.runtime(8514)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        my_id = initiative_logic.user_profile().uuid

        # Created in participant-then-owner order, so a correct sort proves
        # it's reordering rather than accidentally preserving creation order.
        participant_card = initiative_logic.create_card(doing.uuid, "I'm just on it", "", [my_id]).value
        owner_card = initiative_logic.create_card(doing.uuid, "I own this", "", [my_id], owner=my_id).value
        bob.pick_initiative(initiative.uuid, [doing.uuid], [])

        summary = bob.summary_payload()["initiatives"][0]

        self.assertEqual(
            [(c["uuid"], c["relevance"]) for c in summary["active_cards"]],
            [(owner_card.uuid, "owner"), (participant_card.uuid, "participant")],
        )

    def test_card_summary_includes_people_labels(self):
        runtime = self.runtime(8524)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative_logic.session.set_identity("Andrea")
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        my_id = initiative_logic.user_profile().uuid
        initiative_logic.create_card(doing.uuid, "Discuss API", "Choose connector shape", [my_id], owner=my_id)
        bob.pick_initiative(initiative.uuid, [doing.uuid], [])

        card = bob.summary_payload()["initiatives"][0]["active_cards"][0]

        self.assertEqual(card["description"], "Choose connector shape")
        self.assertEqual(card["owner_label"], "Andrea")
        self.assertEqual(card["participant_labels"], ["Andrea"])
        self.assertEqual(card["owner_person"]["name"], "Andrea")
        self.assertEqual(card["participant_people"][0]["name"], "Andrea")
        self.assertNotIn("involved_labels", card)
        self.assertNotIn("involved_people", card)

    def test_summary_payload_lists_known_people_for_the_card_picker(self):
        runtime = self.runtime(8527)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative_logic.session.set_identity("Andrea")
        my_id = initiative_logic.user_profile().uuid

        payload = bob.summary_payload()

        self.assertIn("people", payload)
        self.assertEqual([p["id"] for p in payload["people"]], [my_id])
        self.assertEqual(payload["people"][0]["name"], "Andrea")

    def test_summary_counts_cards_in_discussion(self):
        runtime = self.runtime(8525)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        my_id = initiative_logic.user_profile().uuid
        card = initiative_logic.create_card(doing.uuid, "Discuss me", "", [my_id], owner=my_id).value
        bob.pick_initiative(initiative.uuid, [doing.uuid], [])
        runtime.session.note_indirect_peer_topic("relay:peer", initiative.uuid)
        runtime.session.apply_peer_subtree(
            "relay:peer",
            ProtocolNode.from_dict(runtime.session.protocol.index[initiative.uuid].to_dict()),
            runtime.session.protocol.root.uuid,
        )

        initiative_logic.update_card(card.uuid, "Discuss me locally", "", [my_id], owner=my_id)
        runtime.session.record_peer_observations(
            "relay:peer",
            runtime.session.node_revision_map(runtime.session.protocol.index[initiative.uuid]),
        )

        summary = bob.summary_payload()["initiatives"][0]
        self.assertEqual(summary["discussion_count"], 1)
        self.assertEqual(summary["transition_count"], 1)
        self.assertEqual(summary["transition"]["stage"], "awaiting_peer")
        self.assertEqual(summary["column_count"], 3)
        transition = summary["active_cards"][0]["transition"]
        # My own edit that the peer has observed but not answered: the
        # relation is that I changed it, and it is waiting on them.
        self.assertEqual(transition["type"], "local_made_changes")
        self.assertEqual(transition["stage"], "awaiting_peer")
        self.assertEqual(transition["peer_addr"], "relay:peer")
        perspectives = summary["active_cards"][0]["perspectives"]
        self.assertEqual(len(perspectives), 1)
        self.assertEqual(perspectives[0]["peer_addr"], "relay:peer")
        self.assertFalse(perspectives[0]["absent"])
        self.assertEqual(perspectives[0]["name"], "Discuss me")
        self.assertEqual(perspectives[0]["column_name"], "Doing")

    def test_card_perspectives_include_multiple_absent_versions_and_dedupe_forwarding(self):
        runtime = self.runtime(8528)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        card = initiative_logic.create_card(initiative_logic.columns(initiative)[0].uuid, "Task").value
        revision_a = "revision-a"

        perspectives = bob._card_perspectives(card, {"events": [
            {"type": "peer_missing_node", "peer_addr": "http://a", "peer_revision": revision_a},
            # A forwarded copy of A's same revision must not become a third user toggle.
            {"type": "peer_missing_node", "peer_addr": "http://forwarder", "peer_revision": revision_a},
            {"type": "peer_missing_node", "peer_addr": "http://b", "peer_revision": "revision-b"},
        ]})

        self.assertEqual([item["peer_addr"] for item in perspectives], ["http://a", "http://b"])
        self.assertTrue(all(item["absent"] for item in perspectives))

    def test_selected_flag_is_summary_only_and_toggles(self):
        runtime = self.runtime(8503)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        my_id = initiative_logic.user_profile().uuid
        card = initiative_logic.create_card(doing.uuid, "Task", "", [my_id]).value
        bob.pick_initiative(initiative.uuid, [doing.uuid], [])

        before = bob.summary_payload()["initiatives"][0]["active_cards"][0]
        self.assertFalse(before["selected"])

        result = bob.toggle_selected(card.uuid)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.value)

        after = bob.summary_payload()["initiatives"][0]["active_cards"][0]
        self.assertTrue(after["selected"])
        self.assertNotIn("selected", runtime.session.protocol.index[card.uuid].data)

        toggled_off = bob.toggle_selected(card.uuid)
        self.assertFalse(toggled_off.value)

    def test_unpick_board_collapses_and_leaves_the_real_board_untouched(self):
        runtime = self.runtime(8504)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        bob.pick_initiative(initiative.uuid, [], [])

        bob.unpick_initiative(initiative.uuid)

        summary = bob.summary_payload()["initiatives"][0]
        self.assertEqual(summary["uuid"], initiative.uuid)
        self.assertFalse(summary["expanded"])
        self.assertIn(initiative.uuid, runtime.session.protocol.index)

    def test_reorder_boards_keeps_unmentioned_boards_appended(self):
        runtime = self.runtime(8505)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = initiative_logic.ensure_initiative()
        board_b_uuid = initiative_logic.create_initiative("Board B").value
        board_c_uuid = initiative_logic.create_initiative("Board C").value
        bob.pick_initiative(board_a.uuid, [], [])
        bob.pick_initiative(board_b_uuid, [], [])
        bob.pick_initiative(board_c_uuid, [], [])

        bob.reorder_initiatives([board_c_uuid, board_a.uuid])

        payload = bob.summary_payload()
        self.assertEqual(
            [b["uuid"] for b in payload["initiatives"]],
            [board_c_uuid, board_a.uuid, board_b_uuid],
        )

    def test_reorder_boards_also_works_on_the_collapsed_group(self):
        # reorder_initiatives used to only ever touch expanded initiatives (and force
        # expanded=True on whatever it reordered) - collapsed initiative tiles
        # had no way to be reordered at all. It should now reorder whichever
        # group the given uuids belong to, and leave expanded/collapsed
        # untouched either way.
        runtime = self.runtime(8526)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = initiative_logic.ensure_initiative()
        board_b_uuid = initiative_logic.create_initiative("Board B").value
        board_c_uuid = initiative_logic.create_initiative("Board C").value
        bob.pick_initiative(board_a.uuid, [], [])  # expanded; must stay unaffected

        bob.reorder_initiatives([board_c_uuid, board_b_uuid])

        payload = bob.summary_payload()
        by_uuid = {b["uuid"]: b for b in payload["initiatives"]}
        self.assertTrue(by_uuid[board_a.uuid]["expanded"])
        self.assertFalse(by_uuid[board_c_uuid]["expanded"])
        self.assertFalse(by_uuid[board_b_uuid]["expanded"])
        collapsed_order = [b["uuid"] for b in payload["initiatives"] if not b["expanded"]]
        self.assertEqual(collapsed_order, [board_c_uuid, board_b_uuid])

    def test_boards_and_teams_share_one_tile_order(self):
        runtime = self.runtime(8531)
        initiative_logic: InitiativeLogic = runtime.logic
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        initiative = initiative_logic.ensure_initiative()
        team_uuid = team.create("Working team")

        initial = bob.summary_payload()
        self.assertEqual(
            set(initial["tile_order"]), {initiative.uuid, team_uuid},
        )

        result = bob.reorder_tiles([team_uuid, initiative.uuid])

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            bob.summary_payload()["tile_order"],
            [team_uuid, initiative.uuid],
        )

        duplicate = bob.reorder_tiles([
            team_uuid, team_uuid, initiative.uuid,
        ])
        invalid = bob.reorder_tiles("not-a-list")

        self.assertEqual(
            duplicate.value, [team_uuid, initiative.uuid],
        )
        self.assertEqual(invalid.status, "error")

    def test_summary_drops_a_picked_board_that_no_longer_exists(self):
        runtime = self.runtime(8506)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = initiative_logic.ensure_initiative()
        board_b_uuid = initiative_logic.create_initiative("Board B").value
        bob.pick_initiative(board_a.uuid, [], [])
        bob.pick_initiative(board_b_uuid, [], [])

        initiative_logic.delete_initiative(board_b_uuid)

        payload = bob.summary_payload()
        self.assertEqual([b["uuid"] for b in payload["initiatives"]], [board_a.uuid])

    def test_card_edits_via_initiative_logic_are_reflected_in_next_summary(self):
        runtime = self.runtime(8507)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        my_id = initiative_logic.user_profile().uuid
        card = initiative_logic.create_card(todo.uuid, "Task", "", [my_id]).value
        bob.pick_initiative(initiative.uuid, [doing.uuid], [todo.uuid])

        initiative_logic.update_card(card.uuid, "Renamed", "New desc", [my_id])
        summary = bob.summary_payload()["initiatives"][0]
        self.assertEqual(summary["next_cards"][0]["name"], "Renamed")

        initiative_logic.move_card(card.uuid, doing.uuid, 0)
        summary = bob.summary_payload()["initiatives"][0]
        self.assertEqual(summary["next_cards"], [])
        self.assertEqual(summary["active_cards"][0]["uuid"], card.uuid)

    def test_pick_board_ignores_column_uuids_from_a_different_board(self):
        runtime = self.runtime(8508)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = initiative_logic.ensure_initiative()
        board_b_uuid = initiative_logic.create_initiative("Board B").value
        board_b = runtime.session.protocol.index[board_b_uuid]
        foreign_column = initiative_logic.columns(board_b)[0]

        bob.pick_initiative(board_a.uuid, [foreign_column.uuid], [])

        summary = bob.summary_payload()["initiatives"][0]
        self.assertEqual(summary["active_column_uuids"], [])

    def test_pick_board_does_not_allow_same_column_as_active_and_next(self):
        runtime = self.runtime(8512)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo = initiative_logic.columns(initiative)[0]

        bob.pick_initiative(initiative.uuid, [todo.uuid], [todo.uuid])

        summary = bob.summary_payload()["initiatives"][0]
        self.assertEqual(summary["active_column_uuids"], [todo.uuid])
        self.assertEqual(summary["next_column_uuids"], [])

    def test_collapse_keeps_column_mapping(self):
        runtime = self.runtime(8515)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        todo, doing, done = initiative_logic.columns(initiative)
        bob.update_initiative_settings(
            initiative.uuid,
            expanded=True,
            active_column_uuid=doing.uuid,
            next_column_uuid=todo.uuid,
        )

        bob.update_initiative_settings(initiative.uuid, expanded=False)

        summary = bob.summary_payload()["initiatives"][0]
        self.assertFalse(summary["expanded"])
        self.assertEqual(summary["active_column_uuid"], doing.uuid)
        self.assertEqual(summary["next_column_uuid"], todo.uuid)

    def test_toggle_selected_rejects_unknown_card(self):
        runtime = self.runtime(8509)
        bob = cockpit(runtime)

        result = bob.toggle_selected("does-not-exist")

        self.assertEqual(result.status, "error")

    def test_objective_field_defaults_to_empty_and_is_settable(self):
        runtime = self.runtime(8510)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        bob.pick_initiative(initiative.uuid, [], [])

        self.assertEqual(bob.summary_payload()["initiatives"][0]["objective"], "")

        initiative_logic.set_initiative_objective(initiative.uuid, "Ship the thing")
        self.assertEqual(
            bob.summary_payload()["initiatives"][0]["objective"],
            "Ship the thing",
        )

    def test_selected_topic_drives_cockpit_collaboration_context(self):
        runtime = self.runtime(8522)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        first = initiative_logic.ensure_initiative()
        second_uuid = initiative_logic.create_initiative("Second").value

        initial = bob.summary_payload()
        self.assertEqual(initial["selected_topic"]["uuid"], first.uuid)
        self.assertIn("auto_adopt_mode", initial)

        self.assertEqual(bob.select_topic(second_uuid).status, "ok")
        selected = bob.summary_payload()
        self.assertEqual(selected["selected_topic"]["uuid"], second_uuid)
        self.assertTrue(next(
            item for item in selected["initiatives"] if item["uuid"] == second_uuid
        )["selected_topic"])

    def test_board_tile_counts_its_agenda_items(self):
        # The tile shows divergences and agenda items side by side, so the
        # agenda count has to be per initiative, not just for the selected one.
        runtime = self.runtime(8523)
        initiative_logic: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        initiative = initiative_logic.ensure_initiative()
        other_uuid = initiative_logic.create_initiative("Second").value
        runtime.session.create_agenda_item(initiative.uuid, "Discuss scope")
        runtime.session.create_agenda_item(initiative.uuid, "Discuss dates")

        counts = {
            item["uuid"]: item["agenda_count"]
            for item in bob.summary_payload()["initiatives"]
        }

        self.assertEqual(counts[initiative.uuid], 2)
        self.assertEqual(counts[other_uuid], 0)

    def test_team_tile_reports_agenda_count_and_starts_collapsed(self):
        runtime = self.runtime(8524)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        team_uuid = team.create("Working team")
        runtime.session.create_agenda_item(team_uuid, "Revisit quorum")

        summary = bob.summary_payload()["teams"][0]

        self.assertEqual(summary["uuid"], team_uuid)
        self.assertTrue(summary["is_organization"])
        self.assertEqual(summary["agenda_count"], 1)
        self.assertFalse(summary["expanded"])
        self.assertEqual(summary["sections"], [])
        self.assertIn(
            ("team", "Organization"),
            [
                (kind["application_id"], kind["noun"])
                for kind in bob.summary_payload()["creatable"]
            ],
        )

    def test_cockpit_preserves_team_s_contextual_organization_projection(self):
        runtime = self.runtime(8531)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        organization_uuid = team.create("Cooperative")
        subteam_uuid = team.create("Research")
        team.non_roots.add(subteam_uuid)

        summaries = {
            item["uuid"]: item for item in bob.summary_payload()["teams"]
        }

        self.assertTrue(summaries[organization_uuid]["is_organization"])
        self.assertFalse(summaries[subteam_uuid]["is_organization"])

    def test_team_agenda_items_can_be_reordered_through_the_facade(self):
        runtime = self.runtime(8530)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        team_uuid = team.create("Working team")
        first = team.create_agenda_item(
            team_uuid, "First topic",
        ).value
        second = team.create_agenda_item(
            team_uuid, "Second topic",
        ).value

        result = bob.move_team_agenda_item(second.uuid, 0)

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            [item.uuid for item in runtime.session.agenda_items(team_uuid)],
            [second.uuid, first.uuid],
        )

    def test_agenda_text_can_be_updated_through_application_facades(self):
        runtime = self.runtime(8533)
        team = _StubTeamFacade(runtime.session)
        flow = _StubFlowFacade(runtime.session)
        bob = cockpit(runtime, team, flow)
        team_uuid = team.create("Working team")
        team_item = team.create_agenda_item(team_uuid, "Team wording").value
        process = bob.create_topic(
            "flow", "Election", "integrative-election",
        ).value
        flow_item = flow.create_agenda_item(process, "Flow wording").value

        team_result = bob.update_team_agenda_item(
            team_item.uuid, "Revised team wording",
        )
        flow_result = bob.update_flow_agenda_item(
            flow_item.uuid, "Revised flow wording",
        )

        self.assertEqual(team_result.status, "ok")
        self.assertEqual(flow_result.status, "ok")
        self.assertEqual(
            runtime.session.protocol.index[team_item.uuid].data["text"],
            "Revised team wording",
        )
        self.assertEqual(
            runtime.session.protocol.index[flow_item.uuid].data["text"],
            "Revised flow wording",
        )

    def test_flow_process_is_a_selectable_tile_with_core_agenda(self):
        runtime = self.runtime(8531)
        flow = _StubFlowFacade(runtime.session)
        bob = cockpit(runtime, flow=flow)

        created = bob.create_topic(
            "flow", "Elect secretary", "integrative-election",
        )
        process_uuid = created.value
        agenda = bob.create_flow_agenda_item(
            process_uuid, "Confirm eligibility", "high",
        )
        selected = bob.select_topic(process_uuid)
        payload = bob.summary_payload()

        self.assertEqual(created.status, "ok")
        self.assertEqual(agenda.status, "ok")
        self.assertEqual(selected.status, "ok")
        self.assertEqual(payload["selected_topic"]["uuid"], process_uuid)
        self.assertIn(process_uuid, payload["tile_order"])
        # What a flow starts from arrives with the kind rather than in a
        # payload field of its own: the dialog asks one question about one
        # kind, and every kind answers it the same way.
        flow_kind = next(
            kind for kind in payload["creatable"]
            if kind["application_id"] == "flow"
        )
        self.assertEqual(flow_kind["noun"], "Flow")
        self.assertTrue(flow_kind["template_required"])
        self.assertEqual(
            [item["value"] for item in flow_kind["templates"]],
            ["integrative-election", "minimal-consent"],
        )
        tile = payload["processes"][0]
        self.assertEqual(tile["title"], "Elect secretary")
        self.assertEqual(tile["current_stage"], "Configure participants")
        self.assertEqual(tile["required_from_me"], "Configure participants")
        self.assertEqual(tile["agenda_count"], 1)
        self.assertFalse(tile["expanded"])

        deleted = bob.delete_flow_process(process_uuid)
        self.assertEqual(deleted.status, "ok")
        self.assertEqual(bob.summary_payload()["processes"], [])

    def test_enlarging_an_team_carries_its_whole_document(self):
        runtime = self.runtime(8525)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        team_uuid = team.create("Working team")
        first = team.add_section(team_uuid, "Purpose", order=0)
        team.add_clause(first, "We decide by consent.", order=0)
        team.add_clause(first, "Anyone may add an item.", order=1)
        team.add_section(team_uuid, "Scope", order=1)

        self.assertEqual(
            bob.set_team_expanded(team_uuid, True).status, "ok",
        )
        summary = bob.summary_payload()["teams"][0]

        self.assertTrue(summary["expanded"])
        self.assertEqual(
            [section["title"] for section in summary["sections"]],
            ["Purpose", "Scope"],
        )
        self.assertEqual(
            [clause["text"] for clause in summary["sections"][0]["clauses"]],
            ["We decide by consent.", "Anyone may add an item."],
        )

    def test_enlarging_a_team_carries_its_actors_and_roles_as_well(self):
        # A team is three parts, so the enlarged tile that shows only the
        # team is showing a third of one.
        runtime = self.runtime(8531)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        team_uuid = team.create("Working team")
        secretary = team.add_role(team_uuid, "Secretary", order=0)
        treasurer = team.add_role(team_uuid, "Treasurer", order=1)
        team.hold_role(secretary, "Andrea")
        # Offered but not answered: nobody holds it, and the actor it was
        # offered to is in the team without holding anything.
        team.hold_role(treasurer, "Bo", status="pending")

        self.assertEqual(
            bob.set_team_expanded(team_uuid, True).status, "ok",
        )
        summary = bob.summary_payload()["teams"][0]

        self.assertEqual(
            [(role["name"], role["holders"]) for role in summary["roles"]],
            [("Secretary", ["Andrea"]), ("Treasurer", [])],
        )
        self.assertEqual(
            [
                (actor["name"], actor["roles"], actor["is_observer"])
                for actor in summary["actors"]
            ],
            [("Andrea", ["Secretary"], False), ("Bo", [], True)],
        )

    def test_collapsing_an_team_drops_the_document_again(self):
        runtime = self.runtime(8526)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        team_uuid = team.create("Working team")
        team.add_section(team_uuid, "Purpose")
        role = team.add_role(team_uuid, "Secretary")
        team.hold_role(role, "Andrea")
        bob.set_team_expanded(team_uuid, True)

        bob.set_team_expanded(team_uuid, False)
        summary = bob.summary_payload()["teams"][0]

        self.assertFalse(summary["expanded"])
        self.assertEqual(summary["sections"], [])
        # All three parts are dropped together - carrying actors and roles on
        # a collapsed tile costs the same 1.5s poll the document does.
        self.assertEqual(summary["actors"], [])
        self.assertEqual(summary["roles"], [])

    def test_missing_team_is_ignored_without_mutating_during_a_read(self):
        runtime = self.runtime(8527)
        team = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, team)
        team_uuid = team.create("Working team")
        bob.set_team_expanded(team_uuid, True)

        runtime.session.delete(team_uuid)
        payload = bob.summary_payload()

        self.assertEqual(payload["teams"], [])
        with runtime.session.lock:
            self.assertEqual(
                bob._metadata()["expanded_team_uuids"], [team_uuid],
            )

    def test_set_team_expanded_rejects_an_unknown_team(self):
        runtime = self.runtime(8528)
        bob = cockpit(runtime, _StubTeamFacade(runtime.session))

        result = bob.set_team_expanded("no-such-uuid", True)

        self.assertEqual(result.status, "error")

    def test_team_expansion_needs_the_team_application(self):
        runtime = self.runtime(8529)
        bob = cockpit(runtime)

        result = bob.set_team_expanded("any-uuid", True)

        self.assertEqual(result.status, "error")
        self.assertIn("not active", result.reason)

    @staticmethod
    def runtime(port: int):
        directory = tempfile.TemporaryDirectory()
        config = app_server.load_config(None, "initiative")
        config["storage_file"] = str(Path(directory.name) / f"{port}.json")
        runtime = app_server.create_runtime(port, config)
        runtime._test_tmp = directory
        return runtime


if __name__ == "__main__":
    unittest.main()
