from pathlib import Path

from app.revit.save_result import (
    resolve_fresh_saved_model_path,
    saved_model_name_collision,
    snapshot_folder_files,
)


def test_resolves_extensionless_legacy_saveas_output(tmp_path):
    source = tmp_path / "project_AR.rvt"
    source.write_bytes(b"source")
    target = tmp_path / "result"
    target.mkdir()
    before = snapshot_folder_files(target)
    actual = target / source.stem
    actual.write_bytes(b"saved")

    assert resolve_fresh_saved_model_path(target, str(source), before) == actual


def test_does_not_invent_a_path_when_fresh_saveas_output_is_ambiguous(tmp_path):
    source = tmp_path / "project_AR.rvt"
    source.write_bytes(b"source")
    target = tmp_path / "result"
    target.mkdir()
    before = snapshot_folder_files(target)
    (target / "other-one.rvt").write_bytes(b"saved")
    (target / "other-two.rvt").write_bytes(b"saved")

    assert resolve_fresh_saved_model_path(target, str(source), before) is None


def test_does_not_treat_an_unrelated_extensionless_sidecar_as_a_model(tmp_path):
    source = tmp_path / "project_AR.rvt"
    source.write_bytes(b"source")
    target = tmp_path / "result"
    target.mkdir()
    before = snapshot_folder_files(target)
    (target / "audit").write_bytes(b"metadata")

    assert resolve_fresh_saved_model_path(target, str(source), before) is None


def test_does_not_treat_a_single_unrelated_rvt_as_a_model(tmp_path):
    source = tmp_path / "project_AR.rvt"
    source.write_bytes(b"source")
    target = tmp_path / "result"
    target.mkdir()
    before = snapshot_folder_files(target)
    (target / "another-project.rvt").write_bytes(b"not this save")

    assert resolve_fresh_saved_model_path(target, str(source), before) is None


def test_extensionless_legacy_output_counts_as_a_save_collision(tmp_path):
    source = tmp_path / "project_AR.rvt"
    source.write_bytes(b"source")
    target = tmp_path / "result"
    target.mkdir()
    (target / source.stem).write_bytes(b"old result")

    assert saved_model_name_collision(target, str(source)) is True
