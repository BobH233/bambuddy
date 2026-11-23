"""Restarting mid-print must not replay first-layer or passed progress events."""

from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest

from backend.app import main
from backend.app.services.bambu_mqtt import PrinterState


@pytest.fixture(autouse=True)
def clean_tracking(monkeypatch):
    monkeypatch.setattr(main, "_last_progress_milestone", {})
    monkeypatch.setattr(main, "_first_layer_notified", {})


def frame(layer=7, progress=48, state="RUNNING", connected=True, stage=0):
    result = PrinterState()
    result.connected = connected
    result.state = state
    result.layer_num = layer
    result.total_layers = 56
    result.progress = progress
    result.stg_cur = 0
    result.mc_print_sub_stage = stage
    return result


def test_restart_at_the_reported_seventh_layer_skips_old_events():
    state = frame()
    main._seed_print_notification_baseline(1, state)
    assert not main._take_first_layer_completion(1, 7, state)
    assert main._progress_milestone_to_notify(1, state) is None
    state.progress = 50
    assert main._progress_milestone_to_notify(1, state) == 50
    assert main._progress_milestone_to_notify(1, state) is None


@pytest.mark.parametrize("progress,baseline,next_progress,expected", [
    (10, 0, 25, 25), (25, 25, 50, 50), (50, 50, 75, 75), (90, 75, 95, None),
])
def test_restart_adopts_only_milestones_already_reached(progress, baseline, next_progress, expected):
    state = frame(progress=progress)
    main._seed_print_notification_baseline(1, state)
    assert main._last_progress_milestone[1] == baseline
    assert main._progress_milestone_to_notify(1, state) is None
    state.progress = next_progress
    assert main._progress_milestone_to_notify(1, state) == expected


def test_restart_during_first_layer_still_notifies_when_it_finishes():
    state = frame(layer=1, progress=10)
    main._seed_print_notification_baseline(1, state)
    assert not main._take_first_layer_completion(1, 1, state)
    state.layer_num = 2
    main._seed_print_notification_baseline(1, state)
    assert main._take_first_layer_completion(1, 2, state)
    assert not main._take_first_layer_completion(1, 3, state)


def test_incomplete_startup_layer_data_does_not_arm_a_false_first_layer():
    state = frame(layer=0, progress=48)
    main._seed_print_notification_baseline(1, state)
    assert 1 not in main._first_layer_notified
    assert 1 not in main._last_progress_milestone
    state.layer_num = 7
    main._seed_print_notification_baseline(1, state)
    assert not main._take_first_layer_completion(1, 7, state)
    assert main._progress_milestone_to_notify(1, state) is None


@pytest.mark.parametrize("state,connected", [("unknown", True), ("RUNNING", False)])
def test_unknown_or_disconnected_telemetry_does_not_establish_a_baseline(state, connected):
    main._seed_print_notification_baseline(1, frame(state=state, connected=connected))
    assert not main._first_layer_notified
    assert not main._last_progress_milestone


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "RUNNING"])
async def test_initial_empty_status_does_not_rearm_first_layer(state, monkeypatch):
    monkeypatch.setattr(main, "_last_status_broadcast", {})
    monkeypatch.setattr(main, "_printer_last_connected", {})
    monkeypatch.setattr(main, "_printer_reconciled_since_connect", {})
    manager = MagicMock()
    manager.get_printer.return_value = None
    manager.get_model.return_value = ""
    status = frame(layer=0, progress=0, state=state)
    with (
        patch.object(main, "printer_manager", manager),
        patch.object(main.ws_manager, "send_printer_status", AsyncMock()),
        patch.object(main, "printer_state_to_dict", return_value={"state": state}),
        patch.object(main, "spawn_background_task", side_effect=lambda coro, **kwargs: coro.close()),
    ):
        await main.on_printer_status_change(1, status)
    assert not main._first_layer_notified
    assert not main._last_progress_milestone


def test_reconnect_and_pause_keep_existing_print_tracking():
    main._last_progress_milestone[1] = 25
    main._first_layer_notified[1] = True
    state = frame(progress=70, state="PAUSE")
    main._seed_print_notification_baseline(1, state)
    assert main._last_progress_milestone[1] == 25
    assert not main._take_first_layer_completion(1, 7, state)
    state.state = "RUNNING"
    assert main._progress_milestone_to_notify(1, state) == 50


def test_observed_new_print_can_defer_first_layer_past_calibration():
    main._last_progress_milestone[1] = 0
    main._first_layer_notified[1] = False
    state = frame(layer=2, progress=25, stage=14)
    main._seed_print_notification_baseline(1, state)
    assert not main._take_first_layer_completion(1, 2, state)
    state.mc_print_sub_stage = 0
    assert main._take_first_layer_completion(1, 3, state)


@pytest.mark.asyncio
async def test_next_real_print_rearms_both_notifications():
    main._last_progress_milestone[1] = 75
    main._first_layer_notified[1] = True

    class StopAtNextStep(Exception):
        pass

    with patch.object(main, "_kill_switch_notification_tasks", SimpleNamespace(
        pop=MagicMock(side_effect=StopAtNextStep)
    )), pytest.raises(StopAtNextStep):
        await main.on_print_start(1, {})
    assert main._last_progress_milestone[1] == 0
    assert main._first_layer_notified[1] is False
    state = frame(layer=2, progress=25)
    main._seed_print_notification_baseline(1, state)
    assert main._take_first_layer_completion(1, 2, state)
    assert main._progress_milestone_to_notify(1, state) == 25
