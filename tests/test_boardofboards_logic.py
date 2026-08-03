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
from sovereign.protocol import ProtocolNode


# A5: S-Cockpit may depend on another application only optionally.
# Its own suite must therefore run with S-Initiative absent, which is also the
# only way CI can install it before S-Initiative exists on an index.
requires_initiative = unittest.skipIf(
    InitiativeLogic is None, "S-Initiative is not installed",
)


class _FacadeLookup:
    def __init__(self, kanban, agreement=None, flow=None):
        self.kanban = kanban
        self.agreement = agreement
        self.flow = flow

    def find(self, application_id, facade_api_version):
        if application_id == "initiative" and facade_api_version == 1:
            return self.kanban
        if application_id == "team" and facade_api_version == 1:
            return self.agreement
        if application_id == "flow" and facade_api_version == 1:
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

    def create(self, title):
        node = self.session.create_child(
            self.session.root_uuid(), {"type": "team", "title": title}, {},
        ).value
        self.uuids.append(node.uuid)
        return node.uuid

    def add_section(self, agreement_uuid, title, order=0):
        return self.session.create_child(
            agreement_uuid,
            {"type": "team_section", "title": title, "order": order},
            {},
        ).value.uuid

    def add_clause(self, section_uuid, text, order=0):
        return self.session.create_child(
            section_uuid,
            {"type": "team_clause", "text": text, "order": order},
            {},
        ).value.uuid

    def add_role(self, agreement_uuid, name, order=0):
        return self.session.create_child(
            agreement_uuid,
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

    def agreements(self):
        nodes = [self.session.protocol.index.get(uuid) for uuid in self.uuids]
        return [node for node in nodes if node and not node.deleted]

    def sections(self, agreement):
        return self._ordered(agreement, "team_section")

    def clauses(self, section):
        return self._ordered(section, "team_clause")

    def roles(self, agreement):
        return self._ordered(agreement, "team_role")

    def role_holders(self, agreement, role):
        return list(self.holders.get(role.uuid, []))

    def participants(self, agreement_uuid):
        # Actors are who holds something here, and each is listed with what
        # they hold - an actor holding nothing accepted is an observer.
        agreement = self.session.protocol.index.get(agreement_uuid)
        people = {}
        for role in self.roles(agreement):
            for holder in self.role_holders(agreement, role):
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

    def transition_events(self, agreement_uuid, network=None):
        self.observed_networks.append(network)
        return []

    def transition_by_node(self, events):
        return {}

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

    def create_agenda_item(self, agreement_uuid, text, priority=None):
        return self.session.create_agenda_item(
            agreement_uuid, text, priority,
        )

    def delete_agenda_item(self, item_uuid):
        return self.session.delete_agenda_item(item_uuid)

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

    def create_agenda_item(self, process_uuid, text, priority=None):
        return self.session.create_agenda_item(
            process_uuid, text, priority,
        )

    def delete_agenda_item(self, item_uuid):
        return self.session.delete_agenda_item(item_uuid)

    def set_agenda_item_priority(self, item_uuid, priority):
        return self.session.set_agenda_item_priority(item_uuid, priority)

    def move_agenda_item(self, item_uuid, index):
        return self.session.move_agenda_item(item_uuid, index)


def cockpit(runtime, agreement=None, flow=None):
    return BoardOfBoardsLogic(
        runtime.session,
        runtime.config,
        facades=_FacadeLookup(
            InitiativeFacade(runtime.logic), agreement, flow,
        ),
    )


class CockpitWithoutKanbanTests(unittest.TestCase):
    """Runs whether or not S-Initiative is installed - that is the point."""

    def test_without_kanban_facade_is_empty_and_reports_source_unavailable(self):
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

        self.assertEqual(payload["boards"], [])
        self.assertFalse(payload["sources"]["initiative"]["available"])
        self.assertIn("not active", payload["sources"]["initiative"]["reason"])
        result = bob.reorder_boards([])
        self.assertEqual(result.status, "error")

@requires_initiative
class BoardOfBoardsLogicTests(unittest.TestCase):
    def test_compatibility_payload_uses_explicit_detached_observations(self):
        runtime = self.runtime(8534)
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        agreement_uuid = agreement.create("No nested transport")
        bob.select_topic(agreement_uuid)

        bob.summary_payload()

        self.assertTrue(agreement.observed_networks)
        self.assertTrue(all(
            network == {} for network in agreement.observed_networks
        ))

    def test_application_host_supplies_live_kanban_facade(self):
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
        kanban = runtime.host.instances["initiative"].logic
        kanban.ensure_board()

        self.assertEqual(runtime.host.primary_instance.manifest.application_id,
                         "cockpit")
        self.assertEqual(len(runtime.logic.summary_payload()["boards"]), 1)
        self.assertTrue(
            runtime.logic.summary_payload()["sources"]["initiative"]["available"],
        )

    def test_summary_lists_all_boards_collapsed_by_default(self):
        runtime = self.runtime(8501)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)

        board_a = kanban.ensure_board()
        board_b_uuid = kanban.create_board("Board B").value

        payload = bob.summary_payload()
        self.assertEqual({item["uuid"] for item in payload["boards"]}, {board_a.uuid, board_b_uuid})
        self.assertTrue(all(not item["expanded"] for item in payload["boards"]))

        bob.pick_board(board_a.uuid, [], [])
        payload = bob.summary_payload()
        expanded = [item for item in payload["boards"] if item["expanded"]]
        self.assertEqual([b["uuid"] for b in expanded], [board_a.uuid])

    def test_tiles_and_selected_collaboration_are_separate_payloads(self):
        runtime = self.runtime(8532)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        runtime.session.create_agenda_item(board.uuid, "Discuss timing")

        tiles = bob.tiles_payload()
        context = bob.context_payload()

        self.assertNotIn("agenda_items", tiles)
        self.assertEqual(context["selected_topic"]["uuid"], board.uuid)
        self.assertEqual(len(context["agenda_items"]), 1)
        self.assertIn("agenda_items", bob.summary_payload())

    def test_summary_carries_columns_and_settings_for_each_board(self):
        runtime = self.runtime(8511)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)

        collapsed = next(
            item for item in bob.summary_payload()["boards"] if item["uuid"] == board.uuid
        )
        self.assertFalse(collapsed["expanded"])
        self.assertEqual(
            {c["uuid"] for c in collapsed["columns"]},
            {todo.uuid, doing.uuid, done.uuid},
        )
        self.assertEqual(collapsed["active_column_uuids"], [])

        bob.pick_board(board.uuid, [doing.uuid], [todo.uuid])

        picked = next(
            item for item in bob.summary_payload()["boards"] if item["uuid"] == board.uuid
        )
        self.assertTrue(picked["expanded"])
        self.assertEqual(picked["active_column_uuids"], [doing.uuid])
        self.assertEqual(picked["next_column_uuids"], [todo.uuid])

    def test_active_and_next_bands_partition_by_mapped_columns(self):
        runtime = self.runtime(8502)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        my_id = kanban.user_profile().uuid

        next_card = kanban.create_card(todo.uuid, "Next task", "", [my_id]).value
        active_card = kanban.create_card(doing.uuid, "Active task", "", [my_id]).value
        kanban.create_card(done.uuid, "Done task", "", [my_id])

        bob.pick_board(board.uuid, [doing.uuid], [todo.uuid])

        summary = bob.summary_payload()["boards"][0]
        self.assertEqual([c["uuid"] for c in summary["active_cards"]], [active_card.uuid])
        self.assertEqual([c["uuid"] for c in summary["next_cards"]], [next_card.uuid])

    def test_active_band_includes_all_cards_with_personal_cards_first(self):
        runtime = self.runtime(8513)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        my_id = kanban.user_profile().uuid

        mine = kanban.create_card(doing.uuid, "Mine", "", [my_id]).value
        someone_elses = kanban.create_card(
            doing.uuid, "Someone else's", "", ["other-user-id"],
        ).value
        unassigned = kanban.create_card(doing.uuid, "Unassigned").value
        bob.pick_board(board.uuid, [doing.uuid], [])

        summary = bob.summary_payload()["boards"][0]

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
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        my_id = kanban.user_profile().uuid

        # Created in participant-then-owner order, so a correct sort proves
        # it's reordering rather than accidentally preserving creation order.
        participant_card = kanban.create_card(doing.uuid, "I'm just on it", "", [my_id]).value
        owner_card = kanban.create_card(doing.uuid, "I own this", "", [my_id], owner=my_id).value
        bob.pick_board(board.uuid, [doing.uuid], [])

        summary = bob.summary_payload()["boards"][0]

        self.assertEqual(
            [(c["uuid"], c["relevance"]) for c in summary["active_cards"]],
            [(owner_card.uuid, "owner"), (participant_card.uuid, "participant")],
        )

    def test_card_summary_includes_people_labels(self):
        runtime = self.runtime(8524)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        kanban.session.set_identity("Andrea")
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        my_id = kanban.user_profile().uuid
        kanban.create_card(doing.uuid, "Discuss API", "Choose connector shape", [my_id], owner=my_id)
        bob.pick_board(board.uuid, [doing.uuid], [])

        card = bob.summary_payload()["boards"][0]["active_cards"][0]

        self.assertEqual(card["description"], "Choose connector shape")
        self.assertEqual(card["owner_label"], "Andrea")
        self.assertEqual(card["participant_labels"], ["Andrea"])
        self.assertEqual(card["owner_person"]["name"], "Andrea")
        self.assertEqual(card["participant_people"][0]["name"], "Andrea")
        self.assertNotIn("involved_labels", card)
        self.assertNotIn("involved_people", card)

    def test_summary_payload_lists_known_people_for_the_card_picker(self):
        runtime = self.runtime(8527)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        kanban.session.set_identity("Andrea")
        my_id = kanban.user_profile().uuid

        payload = bob.summary_payload()

        self.assertIn("people", payload)
        self.assertEqual([p["id"] for p in payload["people"]], [my_id])
        self.assertEqual(payload["people"][0]["name"], "Andrea")

    def test_summary_counts_cards_in_discussion(self):
        runtime = self.runtime(8525)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        my_id = kanban.user_profile().uuid
        card = kanban.create_card(doing.uuid, "Discuss me", "", [my_id], owner=my_id).value
        bob.pick_board(board.uuid, [doing.uuid], [])
        runtime.session.note_indirect_peer_topic("relay:peer", board.uuid)
        runtime.session.apply_peer_subtree(
            "relay:peer",
            ProtocolNode.from_dict(runtime.session.protocol.index[board.uuid].to_dict()),
            runtime.session.protocol.root.uuid,
        )

        kanban.update_card(card.uuid, "Discuss me locally", "", [my_id], owner=my_id)
        runtime.session.record_peer_observations(
            "relay:peer",
            runtime.session.node_revision_map(runtime.session.protocol.index[board.uuid]),
        )

        summary = bob.summary_payload()["boards"][0]
        self.assertEqual(summary["discussion_count"], 1)
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
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        card = kanban.create_card(kanban.columns(board)[0].uuid, "Task").value
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
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        my_id = kanban.user_profile().uuid
        card = kanban.create_card(doing.uuid, "Task", "", [my_id]).value
        bob.pick_board(board.uuid, [doing.uuid], [])

        before = bob.summary_payload()["boards"][0]["active_cards"][0]
        self.assertFalse(before["selected"])

        result = bob.toggle_selected(card.uuid)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.value)

        after = bob.summary_payload()["boards"][0]["active_cards"][0]
        self.assertTrue(after["selected"])
        self.assertNotIn("selected", runtime.session.protocol.index[card.uuid].data)

        toggled_off = bob.toggle_selected(card.uuid)
        self.assertFalse(toggled_off.value)

    def test_unpick_board_collapses_and_leaves_the_real_board_untouched(self):
        runtime = self.runtime(8504)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        bob.pick_board(board.uuid, [], [])

        bob.unpick_board(board.uuid)

        summary = bob.summary_payload()["boards"][0]
        self.assertEqual(summary["uuid"], board.uuid)
        self.assertFalse(summary["expanded"])
        self.assertIn(board.uuid, runtime.session.protocol.index)

    def test_reorder_boards_keeps_unmentioned_boards_appended(self):
        runtime = self.runtime(8505)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = kanban.ensure_board()
        board_b_uuid = kanban.create_board("Board B").value
        board_c_uuid = kanban.create_board("Board C").value
        bob.pick_board(board_a.uuid, [], [])
        bob.pick_board(board_b_uuid, [], [])
        bob.pick_board(board_c_uuid, [], [])

        bob.reorder_boards([board_c_uuid, board_a.uuid])

        payload = bob.summary_payload()
        self.assertEqual(
            [b["uuid"] for b in payload["boards"]],
            [board_c_uuid, board_a.uuid, board_b_uuid],
        )

    def test_reorder_boards_also_works_on_the_collapsed_group(self):
        # reorder_boards used to only ever touch expanded boards (and force
        # expanded=True on whatever it reordered) - collapsed board tiles
        # had no way to be reordered at all. It should now reorder whichever
        # group the given uuids belong to, and leave expanded/collapsed
        # untouched either way.
        runtime = self.runtime(8526)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = kanban.ensure_board()
        board_b_uuid = kanban.create_board("Board B").value
        board_c_uuid = kanban.create_board("Board C").value
        bob.pick_board(board_a.uuid, [], [])  # expanded; must stay unaffected

        bob.reorder_boards([board_c_uuid, board_b_uuid])

        payload = bob.summary_payload()
        by_uuid = {b["uuid"]: b for b in payload["boards"]}
        self.assertTrue(by_uuid[board_a.uuid]["expanded"])
        self.assertFalse(by_uuid[board_c_uuid]["expanded"])
        self.assertFalse(by_uuid[board_b_uuid]["expanded"])
        collapsed_order = [b["uuid"] for b in payload["boards"] if not b["expanded"]]
        self.assertEqual(collapsed_order, [board_c_uuid, board_b_uuid])

    def test_boards_and_agreements_share_one_tile_order(self):
        runtime = self.runtime(8531)
        kanban: InitiativeLogic = runtime.logic
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        board = kanban.ensure_board()
        agreement_uuid = agreement.create("Working agreement")

        initial = bob.summary_payload()
        self.assertEqual(
            set(initial["tile_order"]), {board.uuid, agreement_uuid},
        )

        result = bob.reorder_tiles([agreement_uuid, board.uuid])

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            bob.summary_payload()["tile_order"],
            [agreement_uuid, board.uuid],
        )

        duplicate = bob.reorder_tiles([
            agreement_uuid, agreement_uuid, board.uuid,
        ])
        invalid = bob.reorder_tiles("not-a-list")

        self.assertEqual(
            duplicate.value, [agreement_uuid, board.uuid],
        )
        self.assertEqual(invalid.status, "error")

    def test_summary_drops_a_picked_board_that_no_longer_exists(self):
        runtime = self.runtime(8506)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = kanban.ensure_board()
        board_b_uuid = kanban.create_board("Board B").value
        bob.pick_board(board_a.uuid, [], [])
        bob.pick_board(board_b_uuid, [], [])

        kanban.delete_board(board_b_uuid)

        payload = bob.summary_payload()
        self.assertEqual([b["uuid"] for b in payload["boards"]], [board_a.uuid])

    def test_card_edits_via_kanban_logic_are_reflected_in_next_summary(self):
        runtime = self.runtime(8507)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        my_id = kanban.user_profile().uuid
        card = kanban.create_card(todo.uuid, "Task", "", [my_id]).value
        bob.pick_board(board.uuid, [doing.uuid], [todo.uuid])

        kanban.update_card(card.uuid, "Renamed", "New desc", [my_id])
        summary = bob.summary_payload()["boards"][0]
        self.assertEqual(summary["next_cards"][0]["name"], "Renamed")

        kanban.move_card(card.uuid, doing.uuid, 0)
        summary = bob.summary_payload()["boards"][0]
        self.assertEqual(summary["next_cards"], [])
        self.assertEqual(summary["active_cards"][0]["uuid"], card.uuid)

    def test_pick_board_ignores_column_uuids_from_a_different_board(self):
        runtime = self.runtime(8508)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board_a = kanban.ensure_board()
        board_b_uuid = kanban.create_board("Board B").value
        board_b = runtime.session.protocol.index[board_b_uuid]
        foreign_column = kanban.columns(board_b)[0]

        bob.pick_board(board_a.uuid, [foreign_column.uuid], [])

        summary = bob.summary_payload()["boards"][0]
        self.assertEqual(summary["active_column_uuids"], [])

    def test_pick_board_does_not_allow_same_column_as_active_and_next(self):
        runtime = self.runtime(8512)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo = kanban.columns(board)[0]

        bob.pick_board(board.uuid, [todo.uuid], [todo.uuid])

        summary = bob.summary_payload()["boards"][0]
        self.assertEqual(summary["active_column_uuids"], [todo.uuid])
        self.assertEqual(summary["next_column_uuids"], [])

    def test_collapse_keeps_column_mapping(self):
        runtime = self.runtime(8515)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        todo, doing, done = kanban.columns(board)
        bob.update_board_settings(
            board.uuid,
            expanded=True,
            active_column_uuid=doing.uuid,
            next_column_uuid=todo.uuid,
        )

        bob.update_board_settings(board.uuid, expanded=False)

        summary = bob.summary_payload()["boards"][0]
        self.assertFalse(summary["expanded"])
        self.assertEqual(summary["active_column_uuid"], doing.uuid)
        self.assertEqual(summary["next_column_uuid"], todo.uuid)

    def test_legacy_bindings_do_not_overwrite_new_column_settings(self):
        runtime = self.runtime(8535)
        kanban: InitiativeLogic = runtime.logic
        board = kanban.ensure_board()
        todo, doing, _done = kanban.columns(board)
        with runtime.session.lock:
            metadata = runtime.session.application_metadata(
                "cockpit",
            )
            metadata["picked_boards"] = [board.uuid]
            metadata["board_bindings"] = {
                board.uuid: {
                    "active_column_uuids": [],
                    "next_column_uuids": [],
                },
            }
        bob = cockpit(runtime)

        bob.update_board_settings(
            board.uuid,
            active_column_uuid=doing.uuid,
            next_column_uuid=todo.uuid,
        )
        summary = bob.summary_payload()["boards"][0]

        self.assertEqual(summary["active_column_uuid"], doing.uuid)
        self.assertEqual(summary["next_column_uuid"], todo.uuid)
        with runtime.session.lock:
            metadata = runtime.session.application_metadata(
                "cockpit",
            )
            self.assertNotIn("picked_boards", metadata)
            self.assertNotIn("board_bindings", metadata)

    def test_toggle_selected_rejects_unknown_card(self):
        runtime = self.runtime(8509)
        bob = cockpit(runtime)

        result = bob.toggle_selected("does-not-exist")

        self.assertEqual(result.status, "error")

    def test_objective_field_defaults_to_empty_and_is_settable(self):
        runtime = self.runtime(8510)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        bob.pick_board(board.uuid, [], [])

        self.assertEqual(bob.summary_payload()["boards"][0]["objective"], "")

        kanban.set_board_objective(board.uuid, "Ship the thing")
        self.assertEqual(
            bob.summary_payload()["boards"][0]["objective"],
            "Ship the thing",
        )

    def test_selected_topic_drives_cockpit_collaboration_context(self):
        runtime = self.runtime(8522)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        first = kanban.ensure_board()
        second_uuid = kanban.create_board("Second").value

        initial = bob.summary_payload()
        self.assertEqual(initial["selected_topic"]["uuid"], first.uuid)
        self.assertIn("auto_adopt_mode", initial)

        self.assertEqual(bob.select_topic(second_uuid).status, "ok")
        selected = bob.summary_payload()
        self.assertEqual(selected["selected_topic"]["uuid"], second_uuid)
        self.assertTrue(next(
            item for item in selected["boards"] if item["uuid"] == second_uuid
        )["selected_topic"])

    def test_board_tile_counts_its_agenda_items(self):
        # The tile shows divergences and agenda items side by side, so the
        # agenda count has to be per board, not just for the selected one.
        runtime = self.runtime(8523)
        kanban: InitiativeLogic = runtime.logic
        bob = cockpit(runtime)
        board = kanban.ensure_board()
        other_uuid = kanban.create_board("Second").value
        runtime.session.create_agenda_item(board.uuid, "Discuss scope")
        runtime.session.create_agenda_item(board.uuid, "Discuss dates")

        counts = {
            item["uuid"]: item["agenda_count"]
            for item in bob.summary_payload()["boards"]
        }

        self.assertEqual(counts[board.uuid], 2)
        self.assertEqual(counts[other_uuid], 0)

    def test_agreement_tile_reports_agenda_count_and_starts_collapsed(self):
        runtime = self.runtime(8524)
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        agreement_uuid = agreement.create("Working agreement")
        runtime.session.create_agenda_item(agreement_uuid, "Revisit quorum")

        summary = bob.summary_payload()["agreements"][0]

        self.assertEqual(summary["uuid"], agreement_uuid)
        self.assertEqual(summary["agenda_count"], 1)
        self.assertFalse(summary["expanded"])
        self.assertEqual(summary["sections"], [])

    def test_agreement_agenda_items_can_be_reordered_through_the_facade(self):
        runtime = self.runtime(8530)
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        agreement_uuid = agreement.create("Working agreement")
        first = agreement.create_agenda_item(
            agreement_uuid, "First topic",
        ).value
        second = agreement.create_agenda_item(
            agreement_uuid, "Second topic",
        ).value

        result = bob.move_agreement_agenda_item(second.uuid, 0)

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            [item.uuid for item in runtime.session.agenda_items(agreement_uuid)],
            [second.uuid, first.uuid],
        )

    def test_flow_process_is_a_selectable_tile_with_core_agenda(self):
        runtime = self.runtime(8531)
        flow = _StubFlowFacade(runtime.session)
        bob = cockpit(runtime, flow=flow)

        created = bob.create_flow_process(
            "Elect secretary", "integrative-election", "0.2.0",
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
        self.assertIn(
            {"application_id": "flow", "label": "Flow"},
            payload["creatable"],
        )
        tile = payload["processes"][0]
        self.assertEqual(tile["title"], "Elect secretary")
        self.assertEqual(tile["current_stage"], "Configure participants")
        self.assertEqual(tile["required_from_me"], "Configure participants")
        self.assertEqual(tile["agenda_count"], 1)
        self.assertFalse(tile["expanded"])

    def test_enlarging_an_agreement_carries_its_whole_document(self):
        runtime = self.runtime(8525)
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        agreement_uuid = agreement.create("Working agreement")
        first = agreement.add_section(agreement_uuid, "Purpose", order=0)
        agreement.add_clause(first, "We decide by consent.", order=0)
        agreement.add_clause(first, "Anyone may add an item.", order=1)
        agreement.add_section(agreement_uuid, "Scope", order=1)

        self.assertEqual(
            bob.set_agreement_expanded(agreement_uuid, True).status, "ok",
        )
        summary = bob.summary_payload()["agreements"][0]

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
        # agreement is showing a third of one.
        runtime = self.runtime(8531)
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        agreement_uuid = agreement.create("Working agreement")
        secretary = agreement.add_role(agreement_uuid, "Secretary", order=0)
        treasurer = agreement.add_role(agreement_uuid, "Treasurer", order=1)
        agreement.hold_role(secretary, "Andrea")
        # Offered but not answered: nobody holds it, and the actor it was
        # offered to is in the team without holding anything.
        agreement.hold_role(treasurer, "Bo", status="pending")

        self.assertEqual(
            bob.set_agreement_expanded(agreement_uuid, True).status, "ok",
        )
        summary = bob.summary_payload()["agreements"][0]

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

    def test_collapsing_an_agreement_drops_the_document_again(self):
        runtime = self.runtime(8526)
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        agreement_uuid = agreement.create("Working agreement")
        agreement.add_section(agreement_uuid, "Purpose")
        role = agreement.add_role(agreement_uuid, "Secretary")
        agreement.hold_role(role, "Andrea")
        bob.set_agreement_expanded(agreement_uuid, True)

        bob.set_agreement_expanded(agreement_uuid, False)
        summary = bob.summary_payload()["agreements"][0]

        self.assertFalse(summary["expanded"])
        self.assertEqual(summary["sections"], [])
        # All three parts are dropped together - carrying actors and roles on
        # a collapsed tile costs the same 1.5s poll the document does.
        self.assertEqual(summary["actors"], [])
        self.assertEqual(summary["roles"], [])

    def test_missing_agreement_is_ignored_without_mutating_during_a_read(self):
        runtime = self.runtime(8527)
        agreement = _StubTeamFacade(runtime.session)
        bob = cockpit(runtime, agreement)
        agreement_uuid = agreement.create("Working agreement")
        bob.set_agreement_expanded(agreement_uuid, True)

        runtime.session.delete(agreement_uuid)
        payload = bob.summary_payload()

        self.assertEqual(payload["agreements"], [])
        with runtime.session.lock:
            self.assertEqual(
                bob._metadata()["expanded_agreement_uuids"], [agreement_uuid],
            )

    def test_set_agreement_expanded_rejects_an_unknown_agreement(self):
        runtime = self.runtime(8528)
        bob = cockpit(runtime, _StubTeamFacade(runtime.session))

        result = bob.set_agreement_expanded("no-such-uuid", True)

        self.assertEqual(result.status, "error")

    def test_agreement_expansion_needs_the_agreement_application(self):
        runtime = self.runtime(8529)
        bob = cockpit(runtime)

        result = bob.set_agreement_expanded("any-uuid", True)

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
