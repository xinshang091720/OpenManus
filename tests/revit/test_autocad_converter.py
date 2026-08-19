from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates import (
    dwg_room_extractor_main as converter,
)


def test_busy_attach_reuses_existing_cad_without_dispatch(monkeypatch):
    class Cad:
        Version = "23.1"
        HWND = 99

    active_cad = Cad()
    attempts = []

    def get_active_object(prog_id):
        attempts.append(prog_id)
        if len(attempts) == 1:
            raise RuntimeError("-2147418111 被呼叫方拒绝接收呼叫")
        return active_cad

    dispatched = []
    monkeypatch.setattr(converter.win32com.client, "GetActiveObject", get_active_object)
    monkeypatch.setattr(
        converter.win32com.client,
        "Dispatch",
        lambda *_: dispatched.append("dispatch"),
    )
    monkeypatch.setattr(converter.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        converter.win32process, "GetWindowThreadProcessId", lambda _: (1, 2020)
    )
    monkeypatch.delenv("BEESYNC_AUTOCAD_2020_PID", raising=False)

    assert converter._get_running_autocad() is active_cad
    assert attempts == ["AutoCAD.Application.23.1", "AutoCAD.Application.23.1"]
    assert dispatched == []


def test_busy_com_operation_retries_before_returning(monkeypatch):
    attempts = []

    def callback():
        attempts.append("call")
        if len(attempts) == 1:
            raise RuntimeError("-2147418111 被呼叫方拒绝接收呼叫")
        return "done"

    monkeypatch.setattr(converter.time, "sleep", lambda _: None)
    monkeypatch.setattr(converter, "_wait_for_autocad_idle", lambda *_: None)

    assert converter._call_autocad_with_retry("test", callback, object()) == "done"
    assert attempts == ["call", "call"]


def test_open_drawing_keeps_the_compatible_autocad_2020_argument():
    calls = []

    class Documents:
        def Open(self, path, read_only=None):
            calls.append((path, read_only))
            return "document"

    class Cad:
        pass

    cad = Cad()
    cad.Documents = Documents()

    assert converter._open_drawing_with_retry(cad, "C:/plan.dwg") == "document"
    assert calls == [("C:/plan.dwg", False)]
