"""
Board of Boards - portfolio summary view.

Functionality:
  A live channel into all of the user's own initiatives, not a copy.
  Expanded initiatives are shown first with their objective, an Active
  band (cards from columns mapped as "active" for that initiative) and a Next
  band (cards from columns mapped as "next"). Collapsed initiatives follow as
  compact overview tiles. Card edits, moves, and reactions go through the
  versioned Initiative facade. This module owns its controller namespace,
  per-initiative display settings, column band mappings, and the summary-only
  "selected" flag (never part of the real initiative data).

  Each band shows every card in its mapped column. Cards owned by the local
  user come first, followed by cards they participate in, then all others.

Contract:
  Local-only config/state lives in
  session.application_metadata("cockpit"):
    initiative_settings: {
      initiative_uuid: {expanded: bool, active_column_uuid, next_column_uuid, order}
    }
    selected_card_uuids: [card_uuid, ...]
  Persisted and restored through Session's metadata envelope.

Used API:
  The optional, versioned Initiative application facade and session.Session.
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
INITIATIVE_FACADE_API_VERSION = 2
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
        self._initiative_facade_error = ""
        with self.session.lock:
            metadata = self.session.application_metadata(APP_METADATA_KEY)
            stored = metadata.get("initiative_settings", {})
            settings = (
                stored if isinstance(stored, dict) else {}
            )
            metadata["initiative_settings"] = settings

    def _initiative(self):
        if self.facades is None:
            self._initiative_facade_error = "Initiative application is not active"
            return None
        try:
            facade = self.facades.find(
                INITIATIVE_APPLICATION_ID, INITIATIVE_FACADE_API_VERSION,
            )
        except ValueError as exc:
            self._initiative_facade_error = str(exc)
            return None
        self._initiative_facade_error = (
            "" if facade is not None else "Initiative application is not active"
        )
        return facade

    @property
    def initiative(self):
        facade = self._initiative()
        if facade is None:
            raise RuntimeError(self._initiative_facade_error)
        return facade

    def _team(self):
        # Optional, like the Initiative facade: the cockpit shows team tiles
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
        initiatives: list[dict],
        teams: list[dict],
        processes: list[dict] | None = None,
    ) -> list[str]:
        candidates = [
            *(item["uuid"] for item in initiatives),
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
            *(initiative.uuid for initiative in (
                self._initiative().initiatives() if self._initiative() else []
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
            *(initiative.uuid for initiative in (self._initiative().initiatives() if self._initiative() else [])),
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
        initiatives: list[dict],
        teams: list[dict],
        processes: list[dict] | None = None,
    ) -> dict | None:
        topics = [
            *(
                {
                    "uuid": initiative["uuid"],
                    "title": initiative["name"],
                    "application_id": INITIATIVE_APPLICATION_ID,
                }
                for initiative in initiatives
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
            INITIATIVE_APPLICATION_ID: self._initiative,
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
        facade = self._initiative()
        # What the "+ Add new" menu offers, and what each one starts from.
        # Core answers it from what each application registered about its own
        # topics: this had a list of nouns, a per-kind template lookup and
        # three create paths, all of which were restating what the owning
        # application already knew.
        creatable = self.session.topic_kinds()
        teams = self._team_summaries(network_by_topic)
        processes = self._flow_summaries()
        if facade is None:
            selected = self._selected_topic([], teams, processes)
            return {
                "initiatives": [],
                "teams": teams,
                "processes": processes,
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
                        "reason": self._initiative_facade_error,
                    },
                },
            }
        initiatives = facade.initiatives()
        settings_by_initiative = self._normalized_settings(initiatives)
        initiatives_out = [
            self._initiative_summary(
                initiative,
                settings_by_initiative.get(initiative.uuid, {}),
                (
                    None if network_by_topic is None
                    else network_by_topic.get(initiative.uuid, {})
                ),
            )
            for initiative in sorted(
                initiatives,
                key=lambda initiative: (
                    not settings_by_initiative.get(initiative.uuid, {}).get("expanded", False),
                    int(settings_by_initiative.get(initiative.uuid, {}).get("order", 0) or 0),
                    str(initiative.data.get("name", "")),
                    initiative.created_at,
                ),
            )
        ]
        selected = self._selected_topic(initiatives_out, teams, processes)
        for initiative in initiatives_out:
            initiative["selected_topic"] = bool(
                selected and initiative["uuid"] == selected["uuid"]
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
            "initiatives": initiatives_out,
            "teams": teams,
            "processes": processes,
            "tile_order": self._normalized_tile_order(
                initiatives_out, teams, processes,
            ),
            "creatable": creatable,
            # Every peer this session knows about, for the card-edit modal's
            # owner/members picker - not initiative-scoped (unlike initiative.html's
            # picker, which restricts to current initiative peers) since Overview
            # spans every initiative and has no per-initiative peer topic to filter by.
            "people": list(self._people_by_uuid().values()),
            "users": facade.users(),
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
            facade = self._initiative()
            initiatives = [
                {
                    "uuid": initiative.uuid,
                    "name": initiative.data.get("name", ""),
                }
                for initiative in (facade.initiatives() if facade else [])
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
            selected = self._selected_topic(initiatives, teams, processes)
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
        facade = self._initiative()
        team = self._team()
        return [
            *(
                {
                    "uuid": initiative.uuid,
                    "application_id": INITIATIVE_APPLICATION_ID,
                }
                for initiative in (facade.initiatives() if facade else [])
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
        for initiative in payload.get("initiatives", []):
            topic_uuid = initiative.get("uuid")
            grouped = cls._filter_transition_groups(
                initiative.pop("_transition_by_node", {}),
                observations.get(topic_uuid, {}),
                INITIATIVE_APPLICATION_ID,
            )
            discussion_nodes = set(initiative.pop("_discussion_node_uuids", []))
            initiative["discussion_count"] = len(
                discussion_nodes.intersection(grouped)
            )
            for card in [
                *initiative.get("active_cards", []),
                *initiative.get("next_cards", []),
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

    def _initiative_summary(
        self, initiative: ProtocolNode, settings: dict,
        network: dict | None = None,
    ) -> dict:
        columns = self.initiative.columns(initiative)
        columns_by_uuid = {column.uuid: column for column in columns}
        people_by_uuid = self._people_by_uuid()
        transition_by_node = self.initiative.transition_by_node(
            self.initiative.transition_events(initiative.uuid, network)
        )
        active_uuid = settings.get("active_column_uuid")
        next_uuid = settings.get("next_column_uuid")
        active_uuids = [active_uuid] if active_uuid in columns_by_uuid else []
        next_uuids = [next_uuid] if next_uuid in columns_by_uuid and next_uuid != active_uuid else []
        selected = set(self._metadata().get("selected_card_uuids", []))
        my_id = self.initiative.user_profile().uuid
        card_count = 0
        for column in columns:
            card_count += len(self.initiative.cards(column))
        discussion_nodes = self._discussion_card_uuids(initiative, network)
        active_cards = []
        for column_uuid in active_uuids:
            for card in self.initiative.cards(columns_by_uuid[column_uuid]):
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
            for card in self.initiative.cards(columns_by_uuid[column_uuid]):
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
            "uuid": initiative.uuid,
            "application_id": INITIATIVE_APPLICATION_ID,
            "name": initiative.data.get("name", ""),
            "objective": initiative.data.get("objective", ""),
            "expanded": bool(settings.get("expanded", False)),
            "order": int(settings.get("order", 0) or 0),
            "card_count": card_count,
            "discussion_count": len(discussion_nodes),
            "agenda_count": len(self._agenda_items(initiative.uuid)),
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
        self, initiative: ProtocolNode, network: dict | None = None,
    ) -> int:
        return len(self._discussion_card_uuids(initiative, network))

    def _discussion_card_uuids(
        self, initiative: ProtocolNode, network: dict | None = None,
    ) -> set[str]:
        card_uuids = set()
        for event in self.initiative.transition_events(initiative.uuid, network):
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
        for user in self.initiative.users():
            user_id = user.get("profile_uuid") or user.get("id")
            if not user_id:
                continue
            name = user.get("name") or ""
            if name == "?":
                name = "Me" if user_id == self.initiative.user_profile().uuid else ""
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
    def update_initiative_settings(self, initiative_uuid: str,
                              expanded: bool | None = None,
                              active_column_uuid: str | None = None,
                              next_column_uuid: str | None = None) -> SessionResult:
        facade = self._initiative()
        if facade is None:
            return SessionResult("error", reason=self._initiative_facade_error)
        initiative = self.session.protocol.index.get(initiative_uuid)
        if not initiative or initiative.data.get("type") != "initiative":
            return SessionResult("error", reason="initiative not found")
        valid_column_uuids = {column.uuid for column in facade.columns(initiative)}
        metadata = self._metadata()
        settings = metadata.setdefault("initiative_settings", {})
        current = dict(settings.get(initiative_uuid, {}))
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
        settings[initiative_uuid] = current
        return SessionResult("ok", value=initiative_uuid)

    @_session_transaction
    def reorder_initiatives(self, initiative_uuids: list[str]) -> SessionResult:
        # Expanded and collapsed initiatives each have their own left/right
        # ordering in the UI, so reordering only ever touches the group
        # the moved initiative already belongs to - the caller always passes
        # the full uuid list for that one group, never a mix of both.
        facade = self._initiative()
        if facade is None:
            return SessionResult("error", reason=self._initiative_facade_error)
        metadata = self._metadata()
        settings = metadata.setdefault("initiative_settings", {})
        valid_uuids = {initiative.uuid for initiative in facade.initiatives()}
        mentioned = [uuid for uuid in initiative_uuids if uuid in valid_uuids]
        if not mentioned:
            return SessionResult("ok", value=[])
        expanded_flag = bool(settings.get(mentioned[0], {}).get("expanded", False))
        same_group = {
            initiative.uuid for initiative in facade.initiatives()
            if bool(settings.get(initiative.uuid, {}).get("expanded", False)) == expanded_flag
        }
        ordered = [uuid for uuid in mentioned if uuid in same_group]
        ordered.extend(uuid for uuid in sorted(same_group) if uuid not in ordered)
        for order, initiative_uuid in enumerate(ordered):
            item = dict(settings.get(initiative_uuid, {}))
            item["order"] = order
            settings[initiative_uuid] = item
        return SessionResult("ok", value=ordered)

    @_session_transaction
    def pick_initiative(self, initiative_uuid: str,
                   active_column_uuids: list[str] | None = None,
                   next_column_uuids: list[str] | None = None) -> SessionResult:
        return self.update_initiative_settings(
            initiative_uuid,
            expanded=True,
            active_column_uuid=(active_column_uuids or [""])[0],
            next_column_uuid=(next_column_uuids or [""])[0],
        )

    @_session_transaction
    def unpick_initiative(self, initiative_uuid: str) -> SessionResult:
        return self.update_initiative_settings(initiative_uuid, expanded=False)

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

    def set_initiative_objective(
        self, initiative_uuid: str, objective: str,
    ) -> SessionResult:
        facade = self._initiative()
        return (
            facade.set_initiative_objective(initiative_uuid, objective)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def move_card(
        self, card_uuid: str, column_uuid: str, index: int,
    ) -> SessionResult:
        facade = self._initiative()
        return (
            facade.move_card(card_uuid, column_uuid, index)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def react_to_initiative_node(
        self, source_addr: str, node_uuid: str, reaction: str,
        absent: bool = False,
    ) -> SessionResult:
        facade = self._initiative()
        if not facade:
            return SessionResult("error", reason=self._initiative_facade_error)
        if reaction == "rollback":
            return facade.rollback_peer_node(
                source_addr, node_uuid, absent,
            )
        return facade.accept_peer_node(source_addr, node_uuid, absent)

    def drop_topic(self, topic_uuid: str) -> SessionResult:
        """Stop holding a topic without destroying it.

        The Cockpit holds everything this client has, so this is where "I do
        not want this here any more" belongs. It is not a delete: nothing is
        published, the others keep what they have, and a peer who still
        publishes it will offer it back. Core refuses while anything here
        still references the topic, and names how many.

        Deleting stays with the application that owns the topic - the only
        one that knows who may destroy it - and is unaffected by how many
        references exist.
        """
        return self.session.drop_topic(topic_uuid)

    def delete_initiative(self, initiative_uuid: str) -> SessionResult:
        facade = self._initiative()
        return (
            facade.delete_initiative(initiative_uuid)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def delete_card(self, card_uuid: str) -> SessionResult:
        facade = self._initiative()
        return (
            facade.delete_card(card_uuid)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def update_card(
        self, card_uuid: str, name: str, description: str = "",
        participants: list[str] | None = None, owner: str | None = None,
        expected_content_hash: str | None = None,
    ) -> SessionResult:
        facade = self._initiative()
        if not facade:
            return SessionResult("error", reason=self._initiative_facade_error)
        return facade.update_card(
            card_uuid, name, description, list(participants or []), owner,
            expected_content_hash,
        )

    def create_initiative_agenda_item(
        self, initiative_uuid: str, text: str, priority: str | None = None,
    ) -> SessionResult:
        facade = self._initiative()
        return (
            facade.create_agenda_item(text, priority, initiative_uuid)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def delete_initiative_agenda_item(self, item_uuid: str) -> SessionResult:
        facade = self._initiative()
        return (
            facade.delete_agenda_item(item_uuid)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def update_initiative_agenda_item(
        self, item_uuid: str, text: str,
    ) -> SessionResult:
        facade = self._initiative()
        return (
            facade.update_agenda_item(item_uuid, text)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def prioritize_initiative_agenda_item(
        self, item_uuid: str, priority: str | None,
    ) -> SessionResult:
        facade = self._initiative()
        return (
            facade.set_agenda_item_priority(item_uuid, priority)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def move_initiative_agenda_item(
        self, item_uuid: str, index: int,
    ) -> SessionResult:
        facade = self._initiative()
        return (
            facade.move_agenda_item(item_uuid, index)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def set_initiative_auto_adopt(
        self, initiative_uuid: str, mode: str,
    ) -> SessionResult:
        facade = self._initiative()
        return (
            facade.set_auto_adopt_mode(mode, initiative_uuid)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
        )

    def create_topic(
        self, application_id: str, title: str,
        template: str = "", snapshot: dict | None = None,
    ) -> SessionResult:
        """Make one topic of whatever kind, wherever it came from.

        This had six methods - a create, a copy-and-rename and a
        from-snapshot for each of three applications - each reaching a
        facade to say what that application already says about itself.
        Core routes it now, and what starts from nothing, from a template
        or from a file is the owning application's own answer.
        """
        return self.session.create_application_topic(
            application_id, title, template, snapshot,
        )

    def export_initiative_snapshot(
        self, initiative_uuid: str, name: str = "", description: str = "",
    ) -> SessionResult:
        facade = self._initiative()
        export = getattr(facade, "export_snapshot", None) if facade else None
        return (
            export(initiative_uuid, name, description)
            if callable(export) else SessionResult("error", reason="Snapshots are not supported")
        )

    def rename_initiative(self, initiative_uuid: str, name: str) -> SessionResult:
        facade = self._initiative()
        return (
            facade.rename_initiative(initiative_uuid, name)
            if facade else SessionResult("error", reason=self._initiative_facade_error)
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

    def export_flow_snapshot(
        self, process_uuid: str, name: str = "", description: str = "",
    ) -> SessionResult:
        flow = self._flow()
        export = getattr(flow, "export_snapshot", None) if flow else None
        return (
            export(process_uuid, name, description)
            if callable(export) else SessionResult("error", reason="Snapshots are not supported")
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

    def _normalized_settings(self, initiatives: list[ProtocolNode]) -> dict[str, dict]:
        metadata = self._metadata()
        stored = metadata.get("initiative_settings", {})
        settings = (
            {
                uuid: dict(item)
                for uuid, item in stored.items()
                if isinstance(item, dict)
            }
            if isinstance(stored, dict) else {}
        )
        valid_uuids = {initiative.uuid for initiative in initiatives}
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
        for initiative in initiatives:
            item = dict(settings.get(initiative.uuid, {}))
            item.setdefault("expanded", False)
            if "order" not in item:
                item["order"] = next_order
                next_order += 1
            settings[initiative.uuid] = item
        return settings

    def _next_order(self) -> int:
        settings = self._metadata().setdefault("initiative_settings", {})
        orders = [
            int(item.get("order", -1) or 0)
            for item in settings.values()
            if isinstance(item, dict)
        ]
        return (max(orders) + 1) if orders else 0
