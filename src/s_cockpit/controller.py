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

    async def api_pick_board(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.pick_board(
            data["board_uuid"], data.get("active_column_uuids"),
            data.get("next_column_uuids"),
        ))

    async def api_update_board_settings(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.update_board_settings(
            data["board_uuid"],
            data.get("expanded") if "expanded" in data else None,
            data.get("active_column_uuid"),
            data.get("next_column_uuid"),
        ))

    async def api_unpick_board(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.unpick_board(data["board_uuid"]),
        )

    async def api_reorder_boards(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.reorder_boards(data.get("board_uuids", [])),
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

    async def api_set_board_objective(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.set_board_objective(
            data["board_uuid"], data.get("objective", ""),
        ))

    async def api_move_card(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.move_card(
            data["card_uuid"], data["column_uuid"], int(data.get("index", 0)),
        ))

    async def api_adopt_kanban_node(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.react_to_kanban_node(
            data["source_addr"], data["node_uuid"], "adopt",
            bool(data.get("adopt_absence")),
        ))

    async def api_rollback_kanban_node(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.react_to_kanban_node(
            data["source_addr"], data["node_uuid"], "rollback",
            bool(data.get("rollback_absence")),
        ))

    async def api_delete_board(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_board(data["board_uuid"]),
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

    async def api_create_kanban_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.create_kanban_agenda_item(
            data["board_uuid"], data.get("text", ""), data.get("priority"),
        ))

    async def api_delete_kanban_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.delete_kanban_agenda_item(data["item_uuid"]),
        )

    async def api_update_kanban_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.update_kanban_agenda_item(
                data["item_uuid"], data.get("text", ""),
            ),
        )

    async def api_prioritize_kanban_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.prioritize_kanban_agenda_item(
            data["item_uuid"], data.get("priority"),
        ))

    async def api_move_kanban_agenda(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.move_kanban_agenda_item(
            data["item_uuid"], int(data.get("index", 0)),
        ))

    async def api_set_kanban_auto_adopt(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.set_kanban_auto_adopt(
            data["board_uuid"], data.get("mode", "always"),
        ))

    async def api_create_board(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.create_board(data.get("name", "Kanban Board")),
        )

    async def api_copy_board(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.copy_board(data["board_uuid"]),
        )

    async def api_rename_board(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.rename_board(
            data["board_uuid"], data.get("name", "Kanban Board"),
        ))

    async def api_save_board_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.save_board_snapshot(
            data["board_uuid"], data.get("name", ""), data.get("description", ""),
        ))

    async def api_create_board_from_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.create_board_from_snapshot(
            data["snapshot_uuid"], data.get("name", ""),
        ))

    async def api_delete_board_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_board_snapshot(data["snapshot_uuid"]),
        )

    async def api_create_team(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data,
            lambda: logic.create_team(data.get("title", "")),
        )

    async def api_clone_team(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.clone_team(
                data["team_uuid"], data.get("title"),
            ),
        )

    async def api_delete_team(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_team(data["team_uuid"]),
        )

    async def api_save_team_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.save_team_snapshot(
            data["team_uuid"], data.get("name", ""), data.get("description", ""),
        ))

    async def api_create_team_from_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.create_team_from_snapshot(
            data["snapshot_uuid"], data.get("title", ""),
        ))

    async def api_delete_team_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_team_snapshot(data["snapshot_uuid"]),
        )

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

    async def api_create_flow_process(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.create_flow_process(
                data.get("title", "Untitled process"),
                data.get("definition_id", "integrative-election"),
                data.get("definition_version", "0.2.0"),
            ),
        )

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

    async def api_save_flow_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.save_flow_snapshot(
            data["process_uuid"], data.get("name", ""), data.get("description", ""),
        ))

    async def api_create_flow_from_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(runtime, data, lambda: logic.create_flow_from_snapshot(
            data["snapshot_uuid"], data.get("title", ""),
        ))

    async def api_delete_flow_snapshot(request: Request):
        data = await request.json()
        return await _mutation_result(
            runtime, data, lambda: logic.delete_flow_snapshot(data["snapshot_uuid"]),
        )

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
        Route("/api/cockpit/boards/settings", api_update_board_settings,
              methods=["POST"]),
        Route("/api/cockpit/boards/pick", api_pick_board, methods=["POST"]),
        Route("/api/cockpit/boards/unpick", api_unpick_board, methods=["POST"]),
        Route("/api/cockpit/boards/reorder", api_reorder_boards, methods=["POST"]),
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
        Route("/api/cockpit/kanban/boards/set_objective",
              api_set_board_objective, methods=["POST"]),
        Route("/api/cockpit/kanban/cards/move", api_move_card,
              methods=["POST"]),
        Route("/api/cockpit/kanban/adopt", api_adopt_kanban_node,
              methods=["POST"]),
        Route("/api/cockpit/kanban/rollback", api_rollback_kanban_node,
              methods=["POST"]),
        Route("/api/cockpit/kanban/boards/delete", api_delete_board,
              methods=["POST"]),
        Route("/api/cockpit/kanban/cards/delete", api_delete_card,
              methods=["POST"]),
        Route("/api/cockpit/kanban/cards/update", api_update_card,
              methods=["POST"]),
        Route("/api/cockpit/kanban/agenda/create",
              api_create_kanban_agenda, methods=["POST"]),
        Route("/api/cockpit/kanban/agenda/delete",
              api_delete_kanban_agenda, methods=["POST"]),
        Route("/api/cockpit/kanban/agenda/update",
              api_update_kanban_agenda, methods=["POST"]),
        Route("/api/cockpit/kanban/agenda/set_priority",
              api_prioritize_kanban_agenda, methods=["POST"]),
        Route("/api/cockpit/kanban/agenda/move",
              api_move_kanban_agenda, methods=["POST"]),
        Route("/api/cockpit/kanban/auto_adopt",
              api_set_kanban_auto_adopt, methods=["POST"]),
        Route("/api/cockpit/kanban/boards/create", api_create_board,
              methods=["POST"]),
        Route("/api/cockpit/kanban/boards/copy", api_copy_board,
              methods=["POST"]),
        Route("/api/cockpit/kanban/boards/rename", api_rename_board,
              methods=["POST"]),
        Route("/api/cockpit/kanban/snapshots/save", api_save_board_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/kanban/snapshots/create", api_create_board_from_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/kanban/snapshots/delete", api_delete_board_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/team/teams/create",
              api_create_team, methods=["POST"]),
        Route("/api/cockpit/team/teams/clone",
              api_clone_team, methods=["POST"]),
        Route("/api/cockpit/team/teams/delete",
              api_delete_team, methods=["POST"]),
        Route("/api/cockpit/team/snapshots/save", api_save_team_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/team/snapshots/create", api_create_team_from_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/team/snapshots/delete", api_delete_team_snapshot,
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
        Route("/api/cockpit/flow/processes/create",
              api_create_flow_process, methods=["POST"]),
        Route("/api/cockpit/flow/processes/delete",
              api_delete_flow_process, methods=["POST"]),
        Route("/api/cockpit/flow/processes/leave",
              api_leave_flow_process, methods=["POST"]),
        Route("/api/cockpit/flow/snapshots/save", api_save_flow_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/flow/snapshots/create", api_create_flow_from_snapshot,
              methods=["POST"]),
        Route("/api/cockpit/flow/snapshots/delete", api_delete_flow_snapshot,
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
