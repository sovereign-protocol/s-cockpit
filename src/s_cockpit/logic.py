"""
Board of Boards - portfolio summary view.

Functionality:
  A live channel into all of the user's own kanban boards, not a copy.
  Expanded boards are shown first with their objective, an Active
  band (cards from columns mapped as "active" for that board) and a Next
  band (cards from columns mapped as "next"). Collapsed boards follow as
  compact overview tiles. Card edits, moves, and reactions go through the
  versioned Kanban facade. This module owns its controller namespace,
  per-board display settings, column band mappings, and the summary-only
  "selected" flag (never part of the real board data).

  Each band shows every card in its mapped column. Cards owned by the local
  user come first, followed by cards they participate in, then all others.

Contract:
  Local-only config/state lives in
  session.application_metadata("Board of Boards"):
    board_settings: {
      board_uuid: {expanded: bool, active_column_uuid, next_column_uuid, order}
    }
    selected_card_uuids: [card_uuid, ...]
  Persisted and restored through Session's metadata envelope.

Used API:
  The optional, versioned Kanban application facade and session.Session.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Protocol

from sovereign import ProtocolNode, Session, SessionResult


def _session_transaction(method):
    """Run one operation that reads or writes this application's metadata.

    Session hands out the live metadata namespace only to a lock holder, so
    a read-modify-write here has to be one transaction. Core's response
    helpers already establish it for HTTP requests; taking it here as well
    is free (Session.lock is re-entrant) and keeps each operation correct
    when called directly - from a facade, a test, or a future caller that
    is not an HTTP handler.
    """
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.session.lock:
            return method(self, *args, **kwargs)
    return wrapped


COCKPIT_APPLICATION_ID = "cockpit"
APP_METADATA_KEY = COCKPIT_APPLICATION_ID
INITIATIVE_APPLICATION_ID = "initiative"
INITIATIVE_FACADE_API_VERSION = 1
TEAM_APPLICATION_ID = "team"
TEAM_FACADE_API_VERSION = 2
FLOW_APPLICATION_ID = "flow"
FLOW_FACADE_API_VERSION = 1


class FacadeLookup(Protocol):
    def find(self, application_id: str, facade_api_version: int) -> Any | None: ...


class BoardOfBoardsLogic:
    def __init__(self, session: Session, config: dict | None = None,
                 facades: FacadeLookup | None = None):
        self.session = session
        self.config = config or {}
        self.facades = facades
        self._kanban_facade_error = ""
        with self.session.lock:
            metadata = self.session.application_metadata(APP_METADATA_KEY)
            stored = metadata.get("board_settings", {})
            settings = (
                stored if isinstance(stored, dict) else {}
            )
            self._migrate_old_metadata(settings, metadata)
            metadata["board_settings"] = settings

    def _kanban(self):
        if self.facades is None:
            self._kanban_facade_error = "Kanban application is not active"
            return None
        try:
            facade = self.facades.find(
                INITIATIVE_APPLICATION_ID, INITIATIVE_FACADE_API_VERSION,
            )
        except ValueError as exc:
            self._kanban_facade_error = str(exc)
            return None
        self._kanban_facade_error = (
            "" if facade is not None else "Kanban application is not active"
        )
        return facade

    @property
    def kanban(self):
        facade = self._kanban()
        if facade is None:
            raise RuntimeError(self._kanban_facade_error)
        return facade

    def _team(self):
        # Optional, like the Kanban facade: the cockpit shows team tiles
        # only when the team application is active in this host.
        if self.facades is None:
            return None
        try:
            return self.facades.find(
                TEAM_APPLICATION_ID, TEAM_FACADE_API_VERSION,
            )
        except ValueError:
            return None

    def _flow(self):
        if self.facades is None:
            return None
        try:
            return self.facades.find(
                FLOW_APPLICATION_ID, FLOW_FACADE_API_VERSION,
            )
        except ValueError:
            return None

    def _flow_summaries(self) -> list[dict]:
        flow = self._flow()
        if flow is None:
            return []
        summaries = []
        for process in flow.processes():
            summary = dict(flow.process_summary(process))
            summary["expanded"] = False
            summaries.append(summary)
        return summaries

    def _flow_templates(self) -> list[dict]:
        flow = self._flow()
        if flow is None or not callable(getattr(flow, "templates", None)):
            return []
        return [dict(item) for item in flow.templates()]

    def _team_summaries(
        self, network_by_topic: dict[str, dict] | None = None,
    ) -> list[dict]:
        team = self._team()
        if team is None:
            return []
        order = self._team_order()
        nodes = team.teams()
        expanded_uuids = self._team_expanded()
        live = {node.uuid for node in nodes}
        expanded_uuids = [uuid for uuid in expanded_uuids if uuid in live]
        summaries = []
        for node in nodes:
            network = (
                None if network_by_topic is None
                else network_by_topic.get(node.uuid, {})
            )
            events = (
                team.transition_events(node.uuid)
                if network is None
                else team.transition_events(node.uuid, network)
            )
            grouped = team.transition_by_node(events)
            unsettled = sum(
                1 for value in grouped.values()
                if value.get("type") not in (None, "in_agreement")
            )
            expanded = node.uuid in expanded_uuids
            classify = getattr(team, "is_organization", None)
            summaries.append({
                "uuid": node.uuid,
                "title": node.data.get("title", ""),
                "application_id": TEAM_APPLICATION_ID,
                # Organization is contextual vocabulary for a root Team, not
                # another application or Actor kind. Older facade providers
                # predate the projection and can only have supplied roots.
                "is_organization": (
                    bool(classify(node)) if callable(classify) else True
                ),
                "unsettled_count": unsettled,
                "agenda_count": len(self._agenda_items(node.uuid)),
                "expanded": expanded,
                # A team is its team, its actors and its roles, and the
                # enlarged tile shows all three. Only for the tile that is
                # showing them - every summary carries this on a 1.5s poll
                # otherwise.
                "sections": (
                    self._team_sections(team, node) if expanded else []
                ),
                "actors": (
                    self._team_actors(team, node) if expanded else []
                ),
                "roles": (
                    self._team_roles(team, node) if expanded else []
                ),
                "order": order.get(node.uuid, 0),
                "_transition_by_node": grouped,
            })
        summaries.sort(key=lambda item: (item["order"], item["title"]))
        return summaries

    @staticmethod
    def _team_sections(facade, team: ProtocolNode) -> list[dict]:
        return [
            {
                "uuid": section.uuid,
                "title": section.data.get("title", ""),
                "clauses": [
                    {"uuid": clause.uuid, "text": clause.data.get("text", "")}
                    for clause in facade.clauses(section)
                ],
            }
            for section in facade.sections(team)
        ]

    @staticmethod
    def _team_actors(facade, team: ProtocolNode) -> list[dict]:
        """Who is in this team, and what each of them holds.

        Only accepted roles are named here. A tile has room for what is
        settled, not for every offer still being answered - the team
        application is where an offer is acted on.
        """
        return [
            {
                "uuid": person.get("uuid", ""),
                "name": person.get("name", ""),
                "picture": person.get("picture", ""),
                "actor_kind": person.get("actor_kind", "individual"),
                "is_self": bool(person.get("is_self")),
                "is_observer": bool(person.get("is_observer")),
                "roles": [
                    item.get("name", "")
                    for item in person.get("roles", [])
                    if item.get("status") == "accepted"
                ],
            }
            for person in facade.participants(team.uuid)
        ]

    @staticmethod
    def _team_roles(facade, team: ProtocolNode) -> list[dict]:
        """Every role this team defines, and who is holding it."""
        return [
            {
                "uuid": role.uuid,
                "name": role.data.get("name", ""),
                "holders": [
                    holder.get("name", "")
                    for holder in facade.role_holders(team, role)
                    if holder.get("status") == "accepted"
                ],
            }
            for role in facade.roles(team)
        ]

    def _team_order(self, *, mutable: bool = False) -> dict:
        metadata = self._metadata()
        order = (
            metadata.setdefault("team_order", {})
            if mutable else metadata.get("team_order", {})
        )
        if not isinstance(order, dict):
            order = {}
            if mutable:
                metadata["team_order"] = order
        return order

    def _team_expanded(self, *, mutable: bool = False) -> list:
        metadata = self._metadata()
        expanded = (
            metadata.setdefault("expanded_team_uuids", [])
            if mutable else metadata.get("expanded_team_uuids", [])
        )
        if not isinstance(expanded, list):
            expanded = []
            if mutable:
                metadata["expanded_team_uuids"] = expanded
        return expanded

    @_session_transaction
    def set_team_expanded(self, team_uuid: str,
                               expanded: bool) -> SessionResult:
        team = self._team()
        if team is None:
            return SessionResult("error", reason="Team application is not active")
        if team_uuid not in {node.uuid for node in team.teams()}:
            return SessionResult("error", reason="team not found")
        current = self._team_expanded(mutable=True)
        if expanded and team_uuid not in current:
            current.append(team_uuid)
        elif not expanded and team_uuid in current:
            current.remove(team_uuid)
        return SessionResult("ok", value=team_uuid)

    @_session_transaction
    def reorder_teams(self, team_uuids: list[str]) -> SessionResult:
        team = self._team()
        if team is None:
            return SessionResult("error", reason="Team application is not active")
        valid = {node.uuid for node in team.teams()}
        order = self._team_order(mutable=True)
        for position, uuid in enumerate(
            uuid for uuid in team_uuids if uuid in valid
        ):
            order[uuid] = position
        return SessionResult("ok", value=team_uuids)

    def _normalized_tile_order(
        self,
        boards: list[dict],
        teams: list[dict],
        processes: list[dict] | None = None,
    ) -> list[str]:
        candidates = [
            *(item["uuid"] for item in boards),
            *(item["uuid"] for item in teams),
            *(item["uuid"] for item in (processes or [])),
        ]
        valid = set(candidates)
        stored = self._metadata().get("tile_order", [])
        if not isinstance(stored, list):
            stored = []
        order = []
        for uuid in stored:
            if uuid in valid and uuid not in order:
                order.append(uuid)
        order.extend(uuid for uuid in candidates if uuid not in order)
        return order

    @_session_transaction
    def reorder_tiles(self, tile_uuids: list[str]) -> SessionResult:
        if not isinstance(tile_uuids, list):
            return SessionResult("error", reason="tile_uuids must be a list")
        valid = {
            *(board.uuid for board in (
                self._kanban().boards() if self._kanban() else []
            )),
            *(node.uuid for node in (
                self._team().teams() if self._team() else []
            )),
            *(node.uuid for node in (
                self._flow().processes() if self._flow() else []
            )),
        }
        current = self._metadata().get("tile_order", [])
        if not isinstance(current, list):
            current = []
        order = []
        for uuid in tile_uuids:
            if uuid in valid and uuid not in order:
                order.append(uuid)
        order.extend(
            uuid for uuid in current
            if uuid in valid and uuid not in order
        )
        order.extend(sorted(valid - set(order)))
        self._metadata()["tile_order"] = order
        return SessionResult("ok", value=order)

    @_session_transaction
    def select_topic(self, topic_uuid: str) -> SessionResult:
        team = self._team()
        valid = {
            *(board.uuid for board in (self._kanban().boards() if self._kanban() else [])),
            *(node.uuid for node in (team.teams() if team else [])),
            *(node.uuid for node in (
                self._flow().processes() if self._flow() else []
            )),
        }
        if topic_uuid not in valid:
            return SessionResult("error", reason="topic not found")
        self._metadata()["selected_topic_uuid"] = topic_uuid
        return SessionResult("ok", value=topic_uuid)

    def _selected_topic(
        self,
        boards: list[dict],
        teams: list[dict],
        processes: list[dict] | None = None,
    ) -> dict | None:
        topics = [
            *(
                {
                    "uuid": board["uuid"],
                    "title": board["name"],
                    "application_id": INITIATIVE_APPLICATION_ID,
                }
                for board in boards
            ),
            *(
                {
                    "uuid": team["uuid"],
                    "title": team["title"],
                    "application_id": TEAM_APPLICATION_ID,
                }
                for team in teams
            ),
            *(
                {
                    "uuid": process["uuid"],
                    "title": process["title"],
                    "application_id": FLOW_APPLICATION_ID,
                }
                for process in (processes or [])
            ),
        ]
        selected_uuid = self._metadata().get("selected_topic_uuid")
        selected = next(
            (item for item in topics if item["uuid"] == selected_uuid),
            topics[0] if topics else None,
        )
        return selected

    def _collaboration_context(
        self, selected: dict | None, network: dict | None = None,
    ) -> dict:
        if not selected:
            return {
                "agenda_items": [],
                "transition_events": [],
                "transition_by_node": {},
                "known_identities": self.session.known_identities(),
                "identity_uuid": self.session.identity.uuid,
            }
        facade = {
            INITIATIVE_APPLICATION_ID: self._kanban,
            TEAM_APPLICATION_ID: self._team,
            FLOW_APPLICATION_ID: self._flow,
        }.get(selected["application_id"], lambda: None)()
        if not facade:
            return {}
        return (
            facade.collaboration_context(selected["uuid"])
            if network is None
            else facade.collaboration_context(selected["uuid"], network)
        )

    def _agenda_items(self, topic_uuid: str):
        return self.session.agenda_projection(topic_uuid)

    @_session_transaction
    def tiles_payload(
        self, network_by_topic: dict[str, dict] | None = None,
    ) -> dict:
        # This is an authoritative Session builder. Live observations are
        # supplied explicitly by composite_response after Session is released.
        network_by_topic = network_by_topic or {}
        kanban = self._kanban()
        # Which topic-creating applications this host can offer in the
        # "+ Add new" menu - the cockpit itself creates neither, it only
        # routes to whichever facade is present.
        creatable = [
            {"application_id": INITIATIVE_APPLICATION_ID, "label": "Initiative"},
        ]
        if self._team() is not None:
            creatable.append(
                {"application_id": TEAM_APPLICATION_ID, "label": "Organization"}
            )
        if self._flow() is not None:
            creatable.append(
                {
                    "application_id": FLOW_APPLICATION_ID,
                    "label": "Flow",
                }
            )
        teams = self._team_summaries(network_by_topic)
        processes = self._flow_summaries()
        if kanban is None:
            selected = self._selected_topic([], teams, processes)
            return {
                "boards": [],
                "teams": teams,
                "processes": processes,
                "flow_templates": self._flow_templates(),
                "tile_order": self._normalized_tile_order(
                    [], teams, processes,
                ),
                "creatable": creatable,
                "people": [],
                "users": [],
                "selected_topic": selected,
                "sources": {
                    INITIATIVE_APPLICATION_ID: {
                        "available": False,
                        "reason": self._kanban_facade_error,
                    },
                },
            }
        boards = kanban.boards()
        settings_by_board = self._normalized_settings(boards)
        boards_out = [
            self._board_summary(
                board,
                settings_by_board.get(board.uuid, {}),
                (
                    None if network_by_topic is None
                    else network_by_topic.get(board.uuid, {})
                ),
            )
            for board in sorted(
                boards,
                key=lambda board: (
                    not settings_by_board.get(board.uuid, {}).get("expanded", False),
                    int(settings_by_board.get(board.uuid, {}).get("order", 0) or 0),
                    str(board.data.get("name", "")),
                    board.created_at,
                ),
            )
        ]
        selected = self._selected_topic(boards_out, teams, processes)
        for board in boards_out:
            board["selected_topic"] = bool(
                selected and board["uuid"] == selected["uuid"]
            )
        for team in teams:
            team["selected_topic"] = bool(
                selected and team["uuid"] == selected["uuid"]
            )
        for process in processes:
            process["selected_topic"] = bool(
                selected and process["uuid"] == selected["uuid"]
            )
        return {
            "boards": boards_out,
            "teams": teams,
            "processes": processes,
            "flow_templates": self._flow_templates(),
            "tile_order": self._normalized_tile_order(
                boards_out, teams, processes,
            ),
            "creatable": creatable,
            # Every peer this session knows about, for the card-edit modal's
            # owner/members picker - not board-scoped (unlike initiative.html's
            # picker, which restricts to current board peers) since Overview
            # spans every board and has no per-board peer topic to filter by.
            "people": list(self._people_by_uuid().values()),
            "users": kanban.users(),
            "selected_topic": selected,
            "sources": {INITIATIVE_APPLICATION_ID: {"available": True}},
        }

    @_session_transaction
    def context_payload(
        self,
        selected: dict | None = None,
        network_by_topic: dict[str, dict] | None = None,
    ) -> dict:
        network_by_topic = network_by_topic or {}
        if selected is None:
            kanban = self._kanban()
            boards = [
                {
                    "uuid": board.uuid,
                    "name": board.data.get("name", ""),
                }
                for board in (kanban.boards() if kanban else [])
            ]
            team = self._team()
            teams = [
                {
                    "uuid": node.uuid,
                    "title": node.data.get("title", ""),
                }
                for node in (team.teams() if team else [])
            ]
            flow = self._flow()
            processes = [
                {
                    "uuid": node.uuid,
                    "title": node.data.get("title", ""),
                }
                for node in (flow.processes() if flow else [])
            ]
            selected = self._selected_topic(boards, teams, processes)
        return {
            "selected_topic": selected,
            **self._collaboration_context(
                selected,
                (
                    None if network_by_topic is None or not selected
                    else network_by_topic.get(selected["uuid"], {})
                ),
            ),
        }

    @_session_transaction
    def summary_payload(self) -> dict:
        """Compatibility view for consumers that still need one response."""
        payload = self.tiles_payload()
        payload.update(self.context_payload(payload.get("selected_topic")))
        return payload

    @_session_transaction
    def tiles_snapshot(self) -> dict:
        """Build the authoritative tile snapshot and its observation plan."""
        topics = self._topic_descriptors()
        unfiltered = {
            item["uuid"]: {"_include_all": True} for item in topics
        }
        return {
            "payload": self.tiles_payload(unfiltered),
            "topics": topics,
        }

    @_session_transaction
    def context_snapshot(self) -> dict:
        """Build the authoritative context snapshot and its observation plan."""
        topics = self._topic_descriptors()
        unfiltered = {
            item["uuid"]: {"_include_all": True} for item in topics
        }
        return {
            "payload": self.context_payload(network_by_topic=unfiltered),
            "topics": topics,
        }

    @_session_transaction
    def summary_snapshot(self) -> dict:
        topics = self._topic_descriptors()
        unfiltered = {
            item["uuid"]: {"_include_all": True} for item in topics
        }
        payload = self.tiles_payload(unfiltered)
        payload.update(self.context_payload(
            payload.get("selected_topic"), unfiltered,
        ))
        return {"payload": payload, "topics": topics}

    def _topic_descriptors(self) -> list[dict]:
        kanban = self._kanban()
        team = self._team()
        return [
            *(
                {
                    "uuid": board.uuid,
                    "application_id": INITIATIVE_APPLICATION_ID,
                }
                for board in (kanban.boards() if kanban else [])
            ),
            *(
                {
                    "uuid": node.uuid,
                    "application_id": TEAM_APPLICATION_ID,
                }
                for node in (team.teams() if team else [])
            ),
            *(
                {
                    "uuid": node.uuid,
                    "application_id": FLOW_APPLICATION_ID,
                }
                for node in (
                    self._flow().processes() if self._flow() else []
                )
            ),
        ]

    @classmethod
    def merge_observations(
        cls, snapshot: dict, observations: dict[str, dict],
    ) -> dict:
        """Decorate detached Session data with transport liveness."""
        payload = snapshot["payload"]
        policies = {
            item["uuid"]: item["application_id"]
            for item in snapshot.get("topics", [])
        }
        for board in payload.get("boards", []):
            topic_uuid = board.get("uuid")
            grouped = cls._filter_transition_groups(
                board.pop("_transition_by_node", {}),
                observations.get(topic_uuid, {}),
                INITIATIVE_APPLICATION_ID,
            )
            discussion_nodes = set(board.pop("_discussion_node_uuids", []))
            board["discussion_count"] = len(
                discussion_nodes.intersection(grouped)
            )
            for card in [
                *board.get("active_cards", []),
                *board.get("next_cards", []),
            ]:
                transition = grouped.get(card.get("uuid"))
                card["transition"] = transition
                visible_peers = {
                    event.get("peer_addr")
                    for event in (
                        (transition or {}).get("events")
                        or ([transition] if transition else [])
                    )
                }
                card["perspectives"] = [
                    item for item in card.get("perspectives", [])
                    if item.get("peer_addr") in visible_peers
                ]
        for team in payload.get("teams", []):
            topic_uuid = team.get("uuid")
            grouped = cls._filter_transition_groups(
                team.pop("_transition_by_node", {}),
                observations.get(topic_uuid, {}),
                TEAM_APPLICATION_ID,
            )
            team["unsettled_count"] = sum(
                1 for value in grouped.values()
                if value.get("type") not in (None, "in_agreement")
            )
        selected = payload.get("selected_topic") or {}
        selected_uuid = selected.get("uuid")
        if selected_uuid and "transition_by_node" in payload:
            policy = policies.get(
                selected_uuid, selected.get("application_id"),
            )
            payload["transition_events"] = [
                event for event in payload.get("transition_events", [])
                if cls._transition_is_visible(
                    event, observations.get(selected_uuid, {}), policy,
                )
            ]
            payload["transition_by_node"] = cls._filter_transition_groups(
                payload.get("transition_by_node", {}),
                observations.get(selected_uuid, {}),
                policy,
            )
        return payload

    @classmethod
    def _filter_transition_groups(
        cls, grouped: dict, network: dict, policy: str | None,
    ) -> dict:
        filtered = {}
        for node_uuid, group in grouped.items():
            candidates = group.get("events") or [group]
            visible = [
                event for event in candidates
                if cls._transition_is_visible(event, network, policy)
            ]
            if not visible:
                continue
            top = max(
                visible,
                key=lambda event: tuple(
                    event.get("priority") or Session.transition_rank(event),
                ),
            )
            merged = dict(top)
            if any(event.get("type") != "in_agreement" for event in visible):
                merged["events"] = visible
            filtered[node_uuid] = merged
        return filtered

    @staticmethod
    def _transition_is_visible(
        event: dict, network: dict, policy: str | None,
    ) -> bool:
        if event.get("stage") != "in_flight":
            return True
        peer = ((network.get("peers") or {}).get(
            event.get("peer_addr"),
        ) or {})
        state = (peer.get("channel_liveness") or {}).get("state", "unknown")
        if policy == INITIATIVE_APPLICATION_ID:
            return state == "alive"
        return state != "stale"

    def _board_summary(
        self, board: ProtocolNode, settings: dict,
        network: dict | None = None,
    ) -> dict:
        columns = self.kanban.columns(board)
        columns_by_uuid = {column.uuid: column for column in columns}
        people_by_uuid = self._people_by_uuid()
        transition_by_node = self.kanban.transition_by_node(
            self.kanban.transition_events(board.uuid, network)
        )
        active_uuid = settings.get("active_column_uuid")
        next_uuid = settings.get("next_column_uuid")
        active_uuids = [active_uuid] if active_uuid in columns_by_uuid else []
        next_uuids = [next_uuid] if next_uuid in columns_by_uuid and next_uuid != active_uuid else []
        selected = set(self._metadata().get("selected_card_uuids", []))
        my_id = self.kanban.user_profile().uuid
        card_count = 0
        for column in columns:
            card_count += len(self.kanban.cards(column))
        discussion_nodes = self._discussion_card_uuids(board, network)
        active_cards = []
        for column_uuid in active_uuids:
            for card in self.kanban.cards(columns_by_uuid[column_uuid]):
                relevance = self._relevance(card, my_id)
                entry = self._card_summary(
                    card,
                    columns_by_uuid[column_uuid],
                    people_by_uuid,
                    transition_by_node,
                )
                entry["selected"] = card.uuid in selected
                entry["relevance"] = relevance
                active_cards.append(entry)
        next_cards = []
        for column_uuid in next_uuids:
            for card in self.kanban.cards(columns_by_uuid[column_uuid]):
                relevance = self._relevance(card, my_id)
                entry = self._card_summary(
                    card,
                    columns_by_uuid[column_uuid],
                    people_by_uuid,
                    transition_by_node,
                )
                entry["relevance"] = relevance
                next_cards.append(entry)
        # Stable sort: personal cards first, while each group keeps its
        # existing column/order sequence.
        relevance_order = {"owner": 0, "participant": 1, "other": 2}
        active_cards.sort(key=lambda entry: relevance_order[entry["relevance"]])
        next_cards.sort(key=lambda entry: relevance_order[entry["relevance"]])
        return {
            "uuid": board.uuid,
            "application_id": INITIATIVE_APPLICATION_ID,
            "name": board.data.get("name", ""),
            "objective": board.data.get("objective", ""),
            "expanded": bool(settings.get("expanded", False)),
            "order": int(settings.get("order", 0) or 0),
            "card_count": card_count,
            "discussion_count": len(discussion_nodes),
            "agenda_count": len(self._agenda_items(board.uuid)),
            "column_count": len(columns),
            "columns": [
                {"uuid": column.uuid, "name": column.data.get("name", "")}
                for column in columns
            ],
            "active_column_uuids": active_uuids,
            "next_column_uuids": next_uuids,
            "active_column_uuid": active_uuids[0] if active_uuids else "",
            "next_column_uuid": next_uuids[0] if next_uuids else "",
            "active_cards": active_cards,
            "next_cards": next_cards,
            "_transition_by_node": transition_by_node,
            "_discussion_node_uuids": sorted(discussion_nodes),
        }

    @staticmethod
    def _relevance(card: ProtocolNode, my_id: str) -> str:
        if card.data.get("owner") == my_id:
            return "owner"
        if my_id in (card.data.get("participants") or []):
            return "participant"
        return "other"

    def _discussion_card_count(
        self, board: ProtocolNode, network: dict | None = None,
    ) -> int:
        return len(self._discussion_card_uuids(board, network))

    def _discussion_card_uuids(
        self, board: ProtocolNode, network: dict | None = None,
    ) -> set[str]:
        card_uuids = set()
        for event in self.kanban.transition_events(board.uuid, network):
            if event.get("stage") in ("settled", "in_flight"):
                continue
            node_uuid = event.get("node_uuid")
            if not node_uuid:
                continue
            local_node = self.session.protocol.index.get(node_uuid)
            peer_node = self.session.get_cached_peer_subtree(
                event.get("peer_addr"), node_uuid,
            )
            node = local_node or peer_node
            if node and node.data.get("type") == "kanban_card":
                card_uuids.add(node_uuid)
        return card_uuids

    def _people_by_uuid(self) -> dict[str, dict]:
        people = {}
        for user in self.kanban.users():
            user_id = user.get("profile_uuid") or user.get("id")
            if not user_id:
                continue
            name = user.get("name") or ""
            if name == "?":
                name = "Me" if user_id == self.kanban.user_profile().uuid else ""
            people[user_id] = {
                "id": user_id,
                "name": name or self._short_id(user_id),
                "picture": user.get("picture") or "",
            }
        return people

    @staticmethod
    def _short_id(user_id: str | None) -> str:
        if not user_id:
            return ""
        return str(user_id)[:8]

    def _person(self, user_id: str | None, people_by_uuid: dict[str, dict]) -> dict:
        if not user_id:
            return {"id": "", "name": "", "picture": ""}
        return people_by_uuid.get(user_id, {
            "id": user_id,
            "name": self._short_id(user_id),
            "picture": "",
        })

    def _card_summary(
        self,
        card: ProtocolNode,
        column: ProtocolNode,
        people_by_uuid: dict[str, dict],
        transition_by_node: dict,
    ) -> dict:
        participants = list(card.data.get("participants", []))
        owner = card.data.get("owner")
        owner_person = self._person(owner, people_by_uuid)
        participant_people = [self._person(user_id, people_by_uuid) for user_id in participants]
        transition = transition_by_node.get(card.uuid)
        return {
            "uuid": card.uuid,
            "name": card.data.get("name", ""),
            "description": card.data.get("description", ""),
            "participants": participants,
            "participant_labels": [person["name"] for person in participant_people],
            "participant_people": participant_people,
            "owner": owner,
            "owner_label": owner_person["name"] if owner else "",
            "owner_person": owner_person if owner else None,
            "transition": transition,
            "perspectives": self._card_perspectives(card, transition),
            "column_uuid": column.uuid,
            "column_name": column.data.get("name", ""),
        }

    def _card_perspectives(self, card: ProtocolNode, transition: dict | None) -> list[dict]:
        """Return complete peer card versions represented by active differences."""
        if not transition:
            return []
        perspectives = []
        seen = set()
        for event in transition.get("events") or [transition]:
            if not event or event.get("type") == "in_agreement":
                continue
            peer_addr = event.get("peer_addr")
            revision = event.get("peer_revision") or event.get("peer_state_hash")
            signature = revision or f"{peer_addr}:missing"
            if signature in seen:
                continue
            seen.add(signature)
            peer_card = self.session.get_cached_peer_subtree(peer_addr, card.uuid)
            absent = not peer_card or peer_card.deleted
            peer_column = None
            if peer_card and peer_card.parent_uuid:
                peer_column = self.session.get_cached_peer_subtree(
                    peer_addr, peer_card.parent_uuid,
                )
            data = peer_card.data if peer_card and not absent else {}
            perspectives.append({
                "peer_addr": peer_addr,
                "origin_identity": event.get("origin_identity"),
                "revision": revision,
                "absent": absent,
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "participants": list(data.get("participants", [])),
                "owner": data.get("owner"),
                "column_uuid": peer_card.parent_uuid if peer_card and not absent else "",
                "column_name": (
                    peer_column.data.get("name", "")
                    if peer_column and not peer_column.deleted else ""
                ),
            })
        return perspectives

    @_session_transaction
    def update_board_settings(self, board_uuid: str,
                              expanded: bool | None = None,
                              active_column_uuid: str | None = None,
                              next_column_uuid: str | None = None) -> SessionResult:
        kanban = self._kanban()
        if kanban is None:
            return SessionResult("error", reason=self._kanban_facade_error)
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        valid_column_uuids = {column.uuid for column in kanban.columns(board)}
        metadata = self._metadata()
        settings = metadata.setdefault("board_settings", {})
        current = dict(settings.get(board_uuid, {}))
        if "order" not in current:
            current["order"] = self._next_order()
        if expanded is not None:
            current["expanded"] = bool(expanded)
        if active_column_uuid is not None:
            active_uuid = active_column_uuid or ""
            current["active_column_uuid"] = active_uuid if active_uuid in valid_column_uuids else ""
        if next_column_uuid is not None:
            next_uuid = next_column_uuid or ""
            current["next_column_uuid"] = next_uuid if next_uuid in valid_column_uuids else ""
        if current.get("next_column_uuid") == current.get("active_column_uuid"):
            current["next_column_uuid"] = ""
        settings[board_uuid] = current
        return SessionResult("ok", value=board_uuid)

    @_session_transaction
    def reorder_boards(self, board_uuids: list[str]) -> SessionResult:
        # Expanded and collapsed boards each have their own left/right
        # ordering in the UI, so reordering only ever touches the group
        # the moved board already belongs to - the caller always passes
        # the full uuid list for that one group, never a mix of both.
        kanban = self._kanban()
        if kanban is None:
            return SessionResult("error", reason=self._kanban_facade_error)
        metadata = self._metadata()
        settings = metadata.setdefault("board_settings", {})
        valid_uuids = {board.uuid for board in kanban.boards()}
        mentioned = [uuid for uuid in board_uuids if uuid in valid_uuids]
        if not mentioned:
            return SessionResult("ok", value=[])
        expanded_flag = bool(settings.get(mentioned[0], {}).get("expanded", False))
        same_group = {
            board.uuid for board in kanban.boards()
            if bool(settings.get(board.uuid, {}).get("expanded", False)) == expanded_flag
        }
        ordered = [uuid for uuid in mentioned if uuid in same_group]
        ordered.extend(uuid for uuid in sorted(same_group) if uuid not in ordered)
        for order, board_uuid in enumerate(ordered):
            item = dict(settings.get(board_uuid, {}))
            item["order"] = order
            settings[board_uuid] = item
        return SessionResult("ok", value=ordered)

    @_session_transaction
    def pick_board(self, board_uuid: str,
                   active_column_uuids: list[str] | None = None,
                   next_column_uuids: list[str] | None = None) -> SessionResult:
        return self.update_board_settings(
            board_uuid,
            expanded=True,
            active_column_uuid=(active_column_uuids or [""])[0],
            next_column_uuid=(next_column_uuids or [""])[0],
        )

    @_session_transaction
    def unpick_board(self, board_uuid: str) -> SessionResult:
        return self.update_board_settings(board_uuid, expanded=False)

    @_session_transaction
    def toggle_selected(self, card_uuid: str) -> SessionResult:
        card = self.session.protocol.index.get(card_uuid)
        if not card or card.data.get("type") != "kanban_card":
            return SessionResult("error", reason="card not found")
        metadata = self._metadata()
        selected = set(metadata.get("selected_card_uuids", []))
        if card_uuid in selected:
            selected.discard(card_uuid)
            is_selected = False
        else:
            selected.add(card_uuid)
            is_selected = True
        metadata["selected_card_uuids"] = sorted(selected)
        return SessionResult("ok", value=is_selected)

    def set_board_objective(
        self, board_uuid: str, objective: str,
    ) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.set_board_objective(board_uuid, objective)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def move_card(
        self, card_uuid: str, column_uuid: str, index: int,
    ) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.move_card(card_uuid, column_uuid, index)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def react_to_kanban_node(
        self, source_addr: str, node_uuid: str, reaction: str,
        absent: bool = False,
    ) -> SessionResult:
        kanban = self._kanban()
        if not kanban:
            return SessionResult("error", reason=self._kanban_facade_error)
        if reaction == "rollback":
            return kanban.rollback_peer_node(
                source_addr, node_uuid, absent,
            )
        return kanban.accept_peer_node(source_addr, node_uuid, absent)

    def delete_board(self, board_uuid: str) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.delete_board(board_uuid)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def delete_card(self, card_uuid: str) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.delete_card(card_uuid)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def update_card(
        self, card_uuid: str, name: str, description: str = "",
        participants: list[str] | None = None, owner: str | None = None,
        expected_content_hash: str | None = None,
    ) -> SessionResult:
        kanban = self._kanban()
        if not kanban:
            return SessionResult("error", reason=self._kanban_facade_error)
        return kanban.update_card(
            card_uuid, name, description, list(participants or []), owner,
            expected_content_hash,
        )

    def create_kanban_agenda_item(
        self, board_uuid: str, text: str, priority: str | None = None,
    ) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.create_agenda_item(text, priority, board_uuid)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def delete_kanban_agenda_item(self, item_uuid: str) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.delete_agenda_item(item_uuid)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def update_kanban_agenda_item(
        self, item_uuid: str, text: str,
    ) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.update_agenda_item(item_uuid, text)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def prioritize_kanban_agenda_item(
        self, item_uuid: str, priority: str | None,
    ) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.set_agenda_item_priority(item_uuid, priority)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def move_kanban_agenda_item(
        self, item_uuid: str, index: int,
    ) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.move_agenda_item(item_uuid, index)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def set_kanban_auto_adopt(
        self, board_uuid: str, mode: str,
    ) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.set_auto_adopt_mode(mode, board_uuid)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def create_board(self, name: str) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.create_board(name)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def copy_board(self, board_uuid: str) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.copy_board(board_uuid)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def export_board_snapshot(
        self, board_uuid: str, name: str = "", description: str = "",
    ) -> SessionResult:
        kanban = self._kanban()
        export = getattr(kanban, "export_snapshot", None) if kanban else None
        return (
            export(board_uuid, name, description)
            if callable(export) else SessionResult("error", reason="Snapshots are not supported")
        )

    def create_board_from_snapshot(
        self, document: dict, name: str = "",
    ) -> SessionResult:
        kanban = self._kanban()
        create = getattr(kanban, "create_from_snapshot", None) if kanban else None
        return (
            create(document, name)
            if callable(create) else SessionResult("error", reason="Snapshots are not supported")
        )

    def rename_board(self, board_uuid: str, name: str) -> SessionResult:
        kanban = self._kanban()
        return (
            kanban.rename_board(board_uuid, name)
            if kanban else SessionResult("error", reason=self._kanban_facade_error)
        )

    def create_team(self, title: str) -> SessionResult:
        team = self._team()
        return (
            team.create_team(title)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def clone_team(
        self, team_uuid: str, title: str | None = None,
    ) -> SessionResult:
        """Start a new team from an existing one, as a board copy does."""
        team = self._team()
        return (
            team.clone_team(team_uuid, title)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def export_team_snapshot(
        self, team_uuid: str, name: str = "", description: str = "",
    ) -> SessionResult:
        team = self._team()
        export = getattr(team, "export_snapshot", None) if team else None
        return (
            export(team_uuid, name, description)
            if callable(export) else SessionResult("error", reason="Snapshots are not supported")
        )

    def create_team_from_snapshot(
        self, document: dict, title: str = "",
    ) -> SessionResult:
        team = self._team()
        create = getattr(team, "create_from_snapshot", None) if team else None
        return (
            create(document, title)
            if callable(create) else SessionResult("error", reason="Snapshots are not supported")
        )

    def delete_team(self, team_uuid: str) -> SessionResult:
        team = self._team()
        return (
            team.delete_team(team_uuid)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def create_team_agenda_item(
        self, team_uuid: str, text: str,
        priority: str | None = None,
    ) -> SessionResult:
        team = self._team()
        return (
            team.create_agenda_item(team_uuid, text, priority)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def delete_team_agenda_item(self, item_uuid: str) -> SessionResult:
        team = self._team()
        return (
            team.delete_agenda_item(item_uuid)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def update_team_agenda_item(
        self, item_uuid: str, text: str,
    ) -> SessionResult:
        team = self._team()
        return (
            team.update_agenda_item(item_uuid, text)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def prioritize_team_agenda_item(
        self, item_uuid: str, priority: str | None,
    ) -> SessionResult:
        team = self._team()
        return (
            team.set_agenda_item_priority(item_uuid, priority)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def move_team_agenda_item(
        self, item_uuid: str, index: int,
    ) -> SessionResult:
        team = self._team()
        return (
            team.move_agenda_item(item_uuid, index)
            if team else SessionResult(
                "error", reason="Team application is not active",
            )
        )

    def create_flow_process(
        self,
        title: str,
        definition_id: str = "integrative-election",
        definition_version: str = "0.2.0",
    ) -> SessionResult:
        flow = self._flow()
        return (
            flow.create_process(
                title, definition_id, definition_version,
            )
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def export_flow_snapshot(
        self, process_uuid: str, name: str = "", description: str = "",
    ) -> SessionResult:
        flow = self._flow()
        export = getattr(flow, "export_snapshot", None) if flow else None
        return (
            export(process_uuid, name, description)
            if callable(export) else SessionResult("error", reason="Snapshots are not supported")
        )

    def create_flow_from_snapshot(
        self, document: dict, title: str = "",
    ) -> SessionResult:
        flow = self._flow()
        create = getattr(flow, "create_from_snapshot", None) if flow else None
        return (
            create(document, title)
            if callable(create) else SessionResult("error", reason="Snapshots are not supported")
        )

    def delete_flow_process(self, process_uuid: str) -> SessionResult:
        flow = self._flow()
        return (
            flow.delete_process(process_uuid)
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def leave_flow_process(self, process_uuid: str) -> SessionResult:
        flow = self._flow()
        return (
            flow.leave_process(process_uuid)
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def create_flow_agenda_item(
        self, process_uuid: str, text: str, priority: str | None = None,
    ) -> SessionResult:
        flow = self._flow()
        return (
            flow.create_agenda_item(process_uuid, text, priority)
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def delete_flow_agenda_item(self, item_uuid: str) -> SessionResult:
        flow = self._flow()
        return (
            flow.delete_agenda_item(item_uuid)
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def update_flow_agenda_item(
        self, item_uuid: str, text: str,
    ) -> SessionResult:
        flow = self._flow()
        return (
            flow.update_agenda_item(item_uuid, text)
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def prioritize_flow_agenda_item(
        self, item_uuid: str, priority: str | None,
    ) -> SessionResult:
        flow = self._flow()
        return (
            flow.set_agenda_item_priority(item_uuid, priority)
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def move_flow_agenda_item(
        self, item_uuid: str, index: int,
    ) -> SessionResult:
        flow = self._flow()
        return (
            flow.move_agenda_item(item_uuid, index)
            if flow else SessionResult(
                "error", reason="S-Flow application is not active",
            )
        )

    def _metadata(self) -> dict:
        return self.session.application_metadata(APP_METADATA_KEY)

    def _normalized_settings(self, boards: list[ProtocolNode]) -> dict[str, dict]:
        metadata = self._metadata()
        stored = metadata.get("board_settings", {})
        settings = (
            {
                uuid: dict(item)
                for uuid, item in stored.items()
                if isinstance(item, dict)
            }
            if isinstance(stored, dict) else {}
        )
        valid_uuids = {board.uuid for board in boards}
        for stale_uuid in list(settings):
            if stale_uuid not in valid_uuids:
                settings.pop(stale_uuid, None)
        next_order = max(
            (
                int(item.get("order", -1) or 0)
                for item in settings.values()
            ),
            default=-1,
        ) + 1
        for board in boards:
            item = dict(settings.get(board.uuid, {}))
            item.setdefault("expanded", False)
            if "order" not in item:
                item["order"] = next_order
                next_order += 1
            settings[board.uuid] = item
        return settings

    def _migrate_old_metadata(
        self, settings: dict, metadata: dict | None = None,
    ) -> None:
        metadata = metadata if metadata is not None else self._metadata()
        picked = metadata.pop("picked_boards", [])
        bindings = metadata.pop("board_bindings", {})
        picked = picked if isinstance(picked, list) else []
        bindings = bindings if isinstance(bindings, dict) else {}
        for order, board_uuid in enumerate(picked):
            binding = (
                bindings.get(board_uuid, {})
                if isinstance(bindings.get(board_uuid, {}), dict) else {}
            )
            existing = settings.get(board_uuid, {})
            item = dict(existing) if isinstance(existing, dict) else {}
            active = binding.get("active_column_uuids")
            following = binding.get("next_column_uuids")
            item.setdefault("expanded", True)
            item.setdefault("order", order)
            item.setdefault(
                "active_column_uuid",
                active[0] if isinstance(active, list) and active else "",
            )
            item.setdefault(
                "next_column_uuid",
                (
                    following[0]
                    if isinstance(following, list) and following else ""
                ),
            )
            settings[board_uuid] = item

    def _next_order(self) -> int:
        settings = self._metadata().setdefault("board_settings", {})
        orders = [
            int(item.get("order", -1) or 0)
            for item in settings.values()
            if isinstance(item, dict)
        ]
        return (max(orders) + 1) if orders else 0
