"""Starlette controller for S-Cockpit."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def build_routes(logic, runtime) -> list[Route]:
    async def api_summary(request: Request):
        return _composite_response(runtime, logic, logic.summary_snapshot)

    async def api_tiles(request: Request):
        return _composite_response(runtime, logic, logic.tiles_snapshot)

    async def api_context(request: Request):
        return _composite_response(runtime, logic, logic.context_snapshot)

    async def api_pick_initiative(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.pick_initiative(
            data["initiative_uuid"], data.get("active_column_uuids"),
            data.get("next_column_uuids"),
        ))

    async def api_update_initiative_settings(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.update_initiative_settings(
            data["initiative_uuid"],
            data.get("expanded") if "expanded" in data else None,
            data.get("active_column_uuid"),
            data.get("next_column_uuid"),
        ))

    async def api_unpick_initiative(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.unpick_initiative(data["initiative_uuid"]),
        )

    async def api_reorder_initiatives(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.reorder_initiatives(data.get("initiative_uuids", [])),
        )

    async def api_toggle_selected(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.toggle_selected(data["card_uuid"]),
        )

    async def api_reorder_teams(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.reorder_teams(data.get("team_uuids", [])),
        )

    async def api_reorder_tiles(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.reorder_tiles(data.get("tile_uuids", [])),
        )

    async def api_team_settings(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.set_team_expanded(
            data["team_uuid"], bool(data.get("expanded")),
        ))

    async def api_select_topic(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.select_topic(data["topic_uuid"]),
        )

    async def api_set_initiative_objective(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.set_initiative_objective(
            data["initiative_uuid"], data.get("objective", ""),
        ))

    async def api_move_card(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.move_card(
            data["card_uuid"], data["column_uuid"], int(data.get("index", 0)),
        ))

    async def api_adopt_initiative_node(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.react_to_initiative_node(
            data["source_addr"], data["node_uuid"], "adopt",
            bool(data.get("adopt_absence")),
        ))

    async def api_rollback_initiative_node(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.react_to_initiative_node(
            data["source_addr"], data["node_uuid"], "rollback",
            bool(data.get("rollback_absence")),
        ))

    async def api_drop_topic(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.drop_topic(data["topic_uuid"]),
        )

    async def api_delete_initiative(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_initiative(data["initiative_uuid"]),
        )

    async def api_delete_card(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_card(data["card_uuid"]),
        )

    async def api_update_card(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.update_card(
            data["card_uuid"],
            data.get("name", "Card"),
            data.get("description", ""),
            data.get("participants") or [],
            data.get("owner"),
            data.get("expected_content_hash"),
        ))

    async def api_create_initiative_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.create_initiative_agenda_item(
            data["initiative_uuid"], data.get("text", ""), data.get("priority"),
        ))

    async def api_delete_initiative_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.delete_initiative_agenda_item(data["item_uuid"]),
        )

    async def api_update_initiative_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.update_initiative_agenda_item(
                data["item_uuid"], data.get("text", ""),
            ),
        )

    async def api_prioritize_initiative_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.prioritize_initiative_agenda_item(
            data["item_uuid"], data.get("priority"),
        ))

    async def api_move_initiative_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.move_initiative_agenda_item(
            data["item_uuid"], int(data.get("index", 0)),
        ))

    async def api_set_initiative_auto_adopt(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.set_initiative_auto_adopt(
            data["initiative_uuid"], data.get("mode", "always"),
        ))

    # One route for making a topic of any kind. There were eight - a create,
    # a copy and a from-snapshot per application - and which of the three a
    # request meant was decided in the browser.
    async def api_create_topic(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.create_topic(
            data.get("application_id", ""),
            data.get("title", ""),
            data.get("template", ""),
            data.get("snapshot"),
        ))

    async def api_rename_initiative(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.rename_initiative(
            data["initiative_uuid"], data.get("name", "Initiative"),
        ))

    async def api_export_initiative_snapshot(request: Request):
        data = await request.json()
        return _query_result(logic.export_initiative_snapshot(
            data["initiative_uuid"], data.get("name", ""), data.get("description", ""),
        ))

    async def api_delete_team(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_team(data["team_uuid"]),
        )

    async def api_export_team_snapshot(request: Request):
        data = await request.json()
        return _query_result(logic.export_team_snapshot(
            data["team_uuid"], data.get("name", ""), data.get("description", ""),
        ))

    async def api_create_team_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.create_team_agenda_item(
            data["team_uuid"], data.get("text", ""), data.get("priority"),
        ))

    async def api_delete_team_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.delete_team_agenda_item(data["item_uuid"]),
        )

    async def api_update_team_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.update_team_agenda_item(
                data["item_uuid"], data.get("text", ""),
            ),
        )

    async def api_prioritize_team_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.prioritize_team_agenda_item(
                data["item_uuid"], data.get("priority"),
            ),
        )

    async def api_move_team_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.move_team_agenda_item(
            data["item_uuid"], int(data.get("index", 0)),
        ))

    async def api_delete_flow_process(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.delete_flow_process(data["process_uuid"]),
        )

    async def api_leave_flow_process(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.leave_flow_process(data["process_uuid"]),
        )

    async def api_export_flow_snapshot(request: Request):
        data = await request.json()
        return _query_result(logic.export_flow_snapshot(
            data["process_uuid"], data.get("name", ""), data.get("description", ""),
        ))

    async def api_create_flow_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.create_flow_agenda_item(
                data["process_uuid"],
                data.get("text", ""),
                data.get("priority"),
            ),
        )

    async def api_delete_flow_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.delete_flow_agenda_item(data["item_uuid"]),
        )

    async def api_update_flow_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.update_flow_agenda_item(
                data["item_uuid"], data.get("text", ""),
            ),
        )

    async def api_prioritize_flow_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.prioritize_flow_agenda_item(
                data["item_uuid"], data.get("priority"),
            ),
        )

    async def api_move_flow_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.move_flow_agenda_item(
                data["item_uuid"], int(data.get("index", 0)),
            ),
        )

    return [
        Route("/api/cockpit/summary", api_summary),
        Route("/api/cockpit/tiles", api_tiles),
        Route("/api/cockpit/context", api_context),
        Route("/api/cockpit/initiatives/settings", api_update_initiative_settings,
              methods=["POST"]),
        Route("/api/cockpit/initiatives/pick", api_pick_initiative, methods=["POST"]),
        Route("/api/cockpit/initiatives/unpick", api_unpick_initiative, methods=["POST"]),
        Route("/api/cockpit/initiatives/reorder", api_reorder_initiatives, methods=["POST"]),
        Route("/api/cockpit/teams/reorder", api_reorder_teams,
              methods=["POST"]),
        Route("/api/cockpit/tiles/reorder", api_reorder_tiles,
              methods=["POST"]),
        Route("/api/cockpit/teams/settings", api_team_settings,
              methods=["POST"]),
        Route("/api/cockpit/topics/select", api_select_topic,
              methods=["POST"]),
        Route("/api/cockpit/cards/toggle_selected", api_toggle_selected,
              methods=["POST"]),
        Route("/api/cockpit/initiative/initiatives/set_objective",
              api_set_initiative_objective, methods=["POST"]),
        Route("/api/cockpit/initiative/cards/move", api_move_card,
              methods=["POST"]),
        Route("/api/cockpit/initiative/adopt", api_adopt_initiative_node,
              methods=["POST"]),
        Route("/api/cockpit/initiative/rollback", api_rollback_initiative_node,
              methods=["POST"]),
        Route("/api/cockpit/topics/drop", api_drop_topic, methods=["POST"]),
        Route("/api/cockpit/initiative/initiatives/delete", api_delete_initiative,
              methods=["POST"]),
        Route("/api/cockpit/initiative/cards/delete", api_delete_card,
              methods=["POST"]),
        Route("/api/cockpit/initiative/cards/update", api_update_card,
              methods=["POST"]),
        Route("/api/cockpit/initiative/agenda/create",
              api_create_initiative_agenda, methods=["POST"]),
        Route("/api/cockpit/initiative/agenda/delete",
              api_delete_initiative_agenda, methods=["POST"]),
        Route("/api/cockpit/initiative/agenda/update",
              api_update_initiative_agenda, methods=["POST"]),
        Route("/api/cockpit/initiative/agenda/set_priority",
              api_prioritize_initiative_agenda, methods=["POST"]),
        Route("/api/cockpit/initiative/agenda/move",
              api_move_initiative_agenda, methods=["POST"]),
        Route("/api/cockpit/initiative/auto_adopt",
              api_set_initiative_auto_adopt, methods=["POST"]),
        Route("/api/cockpit/topics/create", api_create_topic,
              methods=["POST"]),
        Route("/api/cockpit/initiative/initiatives/rename", api_rename_initiative,
              methods=["POST"]),
        Route("/api/cockpit/initiative/snapshots/export", api_export_initiative_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/team/teams/delete",
              api_delete_team, methods=["POST"]),
        Route("/api/cockpit/team/snapshots/export", api_export_team_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/team/agenda/create",
              api_create_team_agenda, methods=["POST"]),
        Route("/api/cockpit/team/agenda/delete",
              api_delete_team_agenda, methods=["POST"]),
        Route("/api/cockpit/team/agenda/update",
              api_update_team_agenda, methods=["POST"]),
        Route("/api/cockpit/team/agenda/set_priority",
              api_prioritize_team_agenda, methods=["POST"]),
        Route("/api/cockpit/team/agenda/move",
              api_move_team_agenda, methods=["POST"]),
        Route("/api/cockpit/flow/processes/delete",
              api_delete_flow_process, methods=["POST"]),
        Route("/api/cockpit/flow/processes/leave",
              api_leave_flow_process, methods=["POST"]),
        Route("/api/cockpit/flow/snapshots/export", api_export_flow_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/flow/agenda/create",
              api_create_flow_agenda, methods=["POST"]),
        Route("/api/cockpit/flow/agenda/delete",
              api_delete_flow_agenda, methods=["POST"]),
        Route("/api/cockpit/flow/agenda/update",
              api_update_flow_agenda, methods=["POST"]),
        Route("/api/cockpit/flow/agenda/set_priority",
              api_prioritize_flow_agenda, methods=["POST"]),
        Route("/api/cockpit/flow/agenda/move",
              api_move_flow_agenda, methods=["POST"]),
    ]


async def _mutation_result(runtime, data, operation) -> JSONResponse:
    return await runtime.mutation_response(
        operation,
        mutation_id=data.get("mutation_id"),
        invalidates=("tiles", "context"),
    )


def _composite_response(runtime, logic, snapshot_builder):
    def observe(snapshot):
        return {
            item["uuid"]: runtime.collaboration.network_info(item["uuid"])
            for item in snapshot.get("topics", [])
        }

    return runtime.composite_response(
        snapshot_builder,
        observe,
        logic.merge_observations,
    )


def _query_result(result) -> JSONResponse:
    if result.status != "ok":
        return JSONResponse(
            {"status": "error", "reason": str(result.reason or "unknown error")},
            status_code=409,
        )
    payload = {"status": "ok"}
    if result.value is not None:
        payload["value"] = result.value
    return JSONResponse(payload)
