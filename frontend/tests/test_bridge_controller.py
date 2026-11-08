"""HTTP launch/stop contracts and ownership of the single controller session."""

import json
import socket
import threading
from http.client import HTTPConnection
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock

import pytest

import gamegrip_server as bridge


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(bridge, "_session", None)
    http = bridge.Server(("127.0.0.1", 0), bridge.Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()

    def post(path, **body):
        req = Request(f"http://127.0.0.1:{http.server_port}" + path,
                      data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=2)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    post.port = http.server_port
    yield post
    http.shutdown()
    http.server_close()
    worker.join(timeout=2)


def test_python_step_counts_match_each_game():
    assert bridge.parse_line("--- Step 2: Calibration swing #2 of 2 ---") == {
        "type": "step", "kind": "swing", "step": 2, "total_steps": 2,
    }
    assert bridge.parse_line("--- Step 2: Calibration swing #3 of 3 ---")["total_steps"] == 3


def test_other_websites_cannot_launch_desktop_input(server):
    req = Request(f"http://127.0.0.1:{server.port}/api/launch", data=b'{"sid":1}',
                  headers={"Origin": "https://unrelated.example", "Content-Type": "text/plain"})
    with pytest.raises(HTTPError) as failure:
        urlopen(req, timeout=2)
    assert failure.value.code == 403


def test_http_launch_rejects_old_sessions_and_reports_controller_errors(server, monkeypatch):
    controller = SimpleNamespace(launch=Mock())
    session = SimpleNamespace(id=4, stopped=False, connected=True, controller=controller, client_id=None)
    monkeypatch.setattr(bridge, "_session", session)
    assert server("/api/launch", sid=3)[0] == 409
    controller.launch.assert_not_called()
    assert server("/api/launch", sid=4) == (200, {"ok": True})
    controller.launch.assert_called_once()
    controller.launch.side_effect = RuntimeError("Recalibrate before launching")
    code, body = server("/api/launch", sid=4)
    assert code == 409 and "Recalibrate" in body["error"]


def test_stop_only_affects_the_requested_session(server, monkeypatch):
    session = SimpleNamespace(id=4, stop=Mock())
    monkeypatch.setattr(bridge, "_session", session)
    assert server("/api/stop", sid=3) == (200, {"ok": False})
    session.stop.assert_not_called()
    assert server("/api/stop", sid=4) == (200, {"ok": True})
    session.stop.assert_called_once()


def test_losing_the_setup_event_connection_stops_minecraft(server, monkeypatch):
    stopped = threading.Event()
    session = SimpleNamespace(game="minecraft", client_id="owner-page", stop=Mock(side_effect=stopped.set))
    monkeypatch.setattr(bridge, "_session", session)
    connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
    connection.request("GET", "/api/events?client=owner-page")
    client_socket = connection.sock
    response = connection.getresponse()
    assert response.readline() == b"retry: 1000\n"
    # Simulate abrupt tab/network loss, without any pagehide or stop request.
    client_socket.shutdown(socket.SHUT_RDWR)
    connection.close()
    response.close()
    assert stopped.wait(4)
    session.stop.assert_called_once()


def test_session_stop_and_serial_failure_release_controller(monkeypatch):
    session = bridge.Session("minecraft")
    session.controller = SimpleNamespace(stop=Mock())
    session.ser = SimpleNamespace(close=Mock())
    # Exercise the real worker's finally block on a failed read/open.
    monkeypatch.setattr(session, "_open", Mock(side_effect=OSError("disconnected")))
    session._run()
    session.controller.stop.assert_called_once()
    session.ser.close.assert_called_once()
    session.thread = SimpleNamespace(join=Mock())
    session.stop()
    assert session.stopped and session.ready.is_set()
    assert session.controller.stop.call_count == 2


def test_minecraft_records_only_right_and_down_then_uses_same_serial(monkeypatch):
    import numpy as np

    session = bridge.Session("minecraft")
    controller = Mock()
    controller.calibrate.return_value = {"mount": {"horizontal_axis": "gz", "vertical_axis": "gx"}}
    controller.closed = False
    controller.tick.side_effect = lambda *_: setattr(controller, "closed", True)
    monkeypatch.setattr(bridge, "MinecraftController", lambda _: controller)
    monkeypatch.setattr(bridge.dtw, "countdown", lambda: None)
    recordings = [np.tile([0, 0, 1, 0, 0, 0], (30, 1)) for _ in range(3)]
    record = Mock(side_effect=[(rows, 50) for rows in recordings])
    monkeypatch.setattr(bridge.dtw, "record", record)
    session.ser = SimpleNamespace(readline=lambda: b"0,0,1,0,0,0,0\n", reset_input_buffer=Mock())
    session._minecraft()
    assert record.call_count == 3
    assert all(call.args[0] is session.ser for call in record.call_args_list)
    assert all(a is b for a, b in zip(controller.calibrate.call_args.args, recordings))
    controller.tick.assert_called_once()
