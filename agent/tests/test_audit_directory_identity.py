"""Synthetic split-owner regressions; actual Android credentials need device evidence."""
from pathlib import Path
from types import SimpleNamespace
import os
import stat
import pytest
import audit_commit as commit

WRITER = (12345, 2000, 2000, 2000, 2000, (2000, 2000, 2000, 2000), (2000, 2000, 2000, 2000))


def changed_stat(value, **changes):
    values = {key: getattr(value, key) for key in dir(value) if key.startswith("st_")}
    values.update(changes)
    return SimpleNamespace(**values)


def install_split_identity(monkeypatch, parent):
    """Model exactly parent App10394 vs Python-created2000, not uniform FUSE UID."""
    original_stat, original_fstat = Path.stat, os.fstat
    def metadata(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == parent or path.is_relative_to(parent):
            return changed_stat(value, st_uid=10394 if path == parent else 2000,
                                st_gid=1078, st_dev=208,
                                st_mode=value.st_mode | (stat.S_ISGID if stat.S_ISDIR(value.st_mode) else 0))
        return value
    monkeypatch.setattr(Path, "stat", metadata)
    monkeypatch.setattr(commit.os, "fstat", lambda fd: changed_stat(original_fstat(fd), st_uid=2000, st_gid=1078, st_dev=208))
    monkeypatch.setattr(commit, "_storage_writer_identity", lambda parent: WRITER, raising=False)


def test_actual_split_owner_model_must_enter_shared_boundary(tmp_path, monkeypatch):
    install_split_identity(monkeypatch, tmp_path)
    store = commit.PendingStore(tmp_path / ".audit-commit")
    with store.boundary() as entries:
        assert entries == []
    assert (store.root / "boundary.lock").exists()


@pytest.mark.parametrize("field,value", [("st_uid", 10394), ("st_uid", 2001), ("st_dev", 209)])
def test_marker_cannot_use_parent_uid_foreign_uid_or_other_device(tmp_path, monkeypatch, field, value):
    install_split_identity(monkeypatch, tmp_path)
    marker = tmp_path / ".audit-commit"
    marker.mkdir()
    original = Path.stat
    monkeypatch.setattr(Path, "stat", lambda path, *a, **kw:
                        changed_stat(original(path, *a, **kw), **{field: value}) if path == marker else original(path, *a, **kw))
    with pytest.raises(commit.CommitDenied, match="storage_object_owner_or_filesystem_mismatch"):
        with commit.PendingStore(marker).boundary():
            pytest.fail("wrong owner/dev must not enter")
    assert not (marker / "boundary.lock").exists()


@pytest.mark.parametrize("field", ["st_uid", "st_gid", "st_dev", "st_ino", "st_mode"])
def test_fixed_parent_anchor_changes_are_rejected(tmp_path, monkeypatch, field):
    install_split_identity(monkeypatch, tmp_path)
    guard = commit.DirectoryIdentityGuard.capture(tmp_path)
    original = Path.stat
    def changed(path, *a, **kw):
        value = original(path, *a, **kw)
        return changed_stat(value, **{field: getattr(value, field) + 1}) if path == tmp_path else value
    monkeypatch.setattr(Path, "stat", changed)
    with pytest.raises(commit.CommitDenied, match="storage_parent_identity_changed"):
        guard.check_parent()


@pytest.mark.parametrize("target", ["parent", "marker", "lock", "ancestor"])
def test_symlink_or_redirected_ancestor_is_rejected(tmp_path, monkeypatch, target):
    install_split_identity(monkeypatch, tmp_path)
    marker = tmp_path / ".audit-commit"; marker.mkdir()
    path = {"parent": tmp_path, "marker": marker, "lock": marker / "boundary.lock"}.get(target)
    if target == "ancestor":
        original = Path.resolve
        monkeypatch.setattr(Path, "resolve", lambda p, *a, **kw:
                            original(p, *a, **kw) / "redirected" if p == tmp_path else original(p, *a, **kw))
    else:
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: p == path or original(p))
    with pytest.raises(commit.CommitDenied, match="linked_runtime_path|redirected_runtime_path"):
        with commit.PendingStore(marker).boundary():
            pytest.fail("linked path must not enter")


@pytest.mark.parametrize("field,value", [("st_uid", 10394), ("st_uid", 2001), ("st_dev", 209)])
def test_lock_fd_and_path_identity_reject_foreign_owner_or_device(tmp_path, monkeypatch, field, value):
    install_split_identity(monkeypatch, tmp_path)
    original = commit.os.fstat
    monkeypatch.setattr(commit.os, "fstat", lambda fd: changed_stat(original(fd), **{field: value}))
    with pytest.raises(commit.CommitDenied, match="storage_file_identity_changed|storage_object_owner_or_filesystem_mismatch"):
        with commit.PendingStore(tmp_path / ".audit-commit").boundary():
            pytest.fail("foreign lock must not enter")


def test_writer_change_in_boundary_is_rejected(tmp_path, monkeypatch):
    install_split_identity(monkeypatch, tmp_path)
    with pytest.raises(commit.CommitDenied, match="storage_writer_identity_changed"):
        with commit.PendingStore(tmp_path / ".audit-commit").boundary():
            monkeypatch.setattr(commit, "_storage_writer_identity", lambda parent: (*WRITER[:2], 2001, *WRITER[3:]))


def test_same_owner_directory_inode_swap_is_rejected(tmp_path, monkeypatch):
    install_split_identity(monkeypatch, tmp_path)
    marker = tmp_path / ".audit-commit"; marker.mkdir()
    guard = commit.DirectoryIdentityGuard.capture(tmp_path)
    guard.check_directory(marker)
    original = Path.stat
    monkeypatch.setattr(Path, "stat", lambda p, *a, **kw:
                        changed_stat(original(p, *a, **kw), st_ino=999) if p == marker else original(p, *a, **kw))
    with pytest.raises(commit.CommitDenied, match="storage_object_identity_changed"):
        guard.check_directory(marker)


def test_setgid_storage_group_is_not_python_egid(tmp_path, monkeypatch):
    install_split_identity(monkeypatch, tmp_path)
    marker = tmp_path / ".audit-commit"; marker.mkdir()
    guard = commit.DirectoryIdentityGuard.capture(tmp_path)
    info = guard.check_directory(marker)
    assert guard.writer[4] == 2000 and info.st_gid == 1078
    assert guard.owner_uid == 2000 and guard.anchor[2] == 10394


@pytest.mark.parametrize("android,fsuid,accepted", [(True, 2000, True), (True, 2999, False), (False, 2999, True)])
def test_installed_namespace_selects_android_fsuid_without_platform_or_env_guess(tmp_path, monkeypatch, android, fsuid, accepted):
    fake_os = SimpleNamespace(name="posix", getpid=lambda: 12345, getuid=lambda: 2000,
                              geteuid=lambda: 2000, getgid=lambda: 2000, getegid=lambda: 2000)
    monkeypatch.setattr(commit, "os", fake_os)
    monkeypatch.setattr(commit, "runtime_paths", lambda: commit.RuntimePaths(
        tmp_path / ".audit-commit", tmp_path if android else None, "com.example.app" if android else None))
    original = Path.read_text
    def status(path, *a, **kw):
        if str(path).replace("\\", "/") == "/proc/self/status":
            assert android, "ordinary POSIX must not require Android proc witness"
            return f"Uid:\t2000 2000 2000 {fsuid}\nGid:\t2000 2000 2000 2000\n"
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "read_text", status)
    if accepted:
        writer = commit._storage_writer_identity(tmp_path)
        assert writer[2] == 2000
        assert (writer[5] is not None) is android
    else:
        with pytest.raises(commit.CommitDenied, match="storage_writer_fsuid_mismatch"):
            commit._storage_writer_identity(tmp_path)


def test_missing_writer_api_is_explicit_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(commit, "os", SimpleNamespace(name="posix"))
    with pytest.raises(commit.CommitDenied, match="storage_writer_identity_unavailable"):
        commit._storage_writer_identity(tmp_path)


def test_windows_keeps_namespace_owner_without_fake_uid_api(tmp_path, monkeypatch):
    monkeypatch.setattr(commit, "os", SimpleNamespace(name="nt"))
    guard = commit.DirectoryIdentityGuard.capture(tmp_path)
    assert guard.writer is None and guard.owner_uid == tmp_path.lstat().st_uid


def test_ordinary_desktop_missing_debug_parent_is_still_initialized(tmp_path):
    marker = tmp_path / "new-debug" / ".audit-commit"
    with commit.PendingStore(marker).boundary() as entries:
        assert entries == []
    assert (marker / "boundary.lock").exists()


def test_android_missing_parent_is_not_recreated(tmp_path, monkeypatch):
    parent = tmp_path / "missing-files"
    monkeypatch.setattr(commit, "runtime_paths", lambda: commit.RuntimePaths(parent / ".audit-commit", parent, "com.example.app"))
    with pytest.raises(commit.CommitDenied, match="storage_parent_missing"):
        with commit.PendingStore(parent / ".audit-commit").boundary():
            pytest.fail("Android installed files parent must already exist")
    assert not parent.exists()


def test_writer_drift_after_durable_reserve_refuses_before_purchase_input(tmp_path, monkeypatch):
    from test_audit_purchase import Fixture
    f = Fixture(tmp_path)
    f.emitter.callback = f.ack
    install_split_identity(monkeypatch, tmp_path)
    original = commit.PendingStore.reserve
    def changed(store, value):
        original(store, value)
        monkeypatch.setattr(commit, "_storage_writer_identity", lambda parent: (*WRITER[:2], 2001, *WRITER[3:]))
    monkeypatch.setattr(commit.PendingStore, "reserve", changed)
    with pytest.raises(commit.CommitDenied, match="storage_writer_identity_changed"):
        f.run()
    assert f.inputs == [] and (f.paths.marker_root / "pending.claim").exists()


def test_d14_max_uses_same_foreign_owner_rejection(tmp_path, monkeypatch):
    from test_audit_purchase import Fixture
    f = Fixture(tmp_path)
    f.paths.marker_root.mkdir()
    install_split_identity(monkeypatch, tmp_path)
    original = Path.stat
    monkeypatch.setattr(Path, "stat", lambda p, *a, **kw:
                        changed_stat(original(p, *a, **kw), st_uid=10394) if p == f.paths.marker_root else original(p, *a, **kw))
    with pytest.raises(commit.CommitDenied, match="storage_object_owner_or_filesystem_mismatch"):
        f.gate().skip_audit_maximum_selection(f.context, f.argv, f.node)
    assert f.inputs == []


@pytest.mark.parametrize("mode", ["normal_stop", "audit_stop", "audit_time_advance"])
def test_identity_io_cannot_bypass_final_stop_or_time_gates(tmp_path, monkeypatch, mode):
    from test_audit_purchase import Fixture
    f = Fixture(tmp_path)
    if mode == "normal_stop":
        from audit_config import AuditConfig
        f.config = AuditConfig()
        f.node["attach"] = {"audit_run": {"enabled": False}}
    else:
        f.emitter.callback = f.ack
    original = commit.PendingStore._check_identity
    identity_io_reached = []
    def delayed_identity(store):
        original(store)
        # Model latency/Stop *inside* the direct final identity I/O call, not
        # boundary setup/cleanup. Only the real execute caller is targeted.
        import sys
        if sys._getframe(1).f_code.co_name == "execute":
            identity_io_reached.append(True)
            if mode == "audit_time_advance":
                f.clock.value += 5000
            else:
                f.context.tasker.stopping = True
    monkeypatch.setattr(commit.PendingStore, "_check_identity", delayed_identity)
    with pytest.raises(commit.CommitDenied):
        if mode == "normal_stop":
            f.run(lambda: f.inputs.append("normal") or True)
        else:
            f.run()
    assert identity_io_reached == [True]
    assert f.inputs == []
