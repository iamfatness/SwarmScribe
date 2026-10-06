"""The credential's trust check on Windows: access control lists, read and set for real. No
administrator rights are needed: every folder here is this account's own."""

import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows access control lists; POSIX modes are in test_credentials"
)

EVERYONE = "S-1-1-0"


def stored():
    from swarmscribe_follower.credentials import Stored

    return Stored(
        leader_url="https://leader.example.org", follower_id="f-1", credential="secret-1",
        device="cpu", heartbeat_interval=30, lease_seconds=120,
    )


def let_everyone_in(path) -> None:
    flags = "(OI)(CI)R" if path.is_dir() else "R"
    subprocess.run(
        ["icacls", str(path), "/grant", f"*{EVERYONE}:{flags}"], check=True, capture_output=True
    )


def test_this_account_has_a_sid_and_a_folder_it_made_private_is_trusted(tmp_path):
    from swarmscribe_follower import winacl
    from swarmscribe_follower.fsutil import private_folder

    assert winacl.current_user().startswith("S-1-5-")
    private_folder(tmp_path / "state")
    owner, allowed = winacl.read_acl(tmp_path / "state")
    assert owner == winacl.current_user()
    # Python 3.12.4 and later make a folder of mode 0700 with an access list of the owner,
    # SYSTEM and Administrators; an older one inherits its parent's list. private_folder makes
    # the list private either way (test_a_new_folder_is_made_private_where_python_inherits).
    trusted = {winacl.current_user(), winacl.SYSTEM, winacl.ADMINISTRATORS}
    assert set(allowed) <= trusted | winacl.OWNER_PLACEHOLDERS
    assert winacl.access_problem(tmp_path / "state") is None


def test_a_folder_others_can_reach_is_refused_with_the_command_that_fixes_it(tmp_path):
    from swarmscribe_follower import winacl

    folder = tmp_path / "state"
    folder.mkdir()
    let_everyone_in(folder)
    problem = winacl.access_problem(folder)
    assert "can be reached by other accounts (Everyone (S-1-1-0))" in problem
    assert winacl.access_problem(folder, owner_only=True) is None
    command = problem.split("make it private with: ")[1]
    assert command.startswith(f'icacls "{folder}" /inheritance:r /grant:r')
    assert command.endswith(f'/remove "*{EVERYONE}"')
    subprocess.run(command, check=True, capture_output=True)  # the printed command works
    assert winacl.access_problem(folder) is None


def test_making_a_folder_private_shuts_everyone_else_out_of_it_and_of_what_is_in_it(tmp_path):
    from swarmscribe_follower import winacl

    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "inside.txt").write_text("x", encoding="utf-8")
    let_everyone_in(folder)
    assert winacl.access_problem(folder / "inside.txt", what="file") is not None  # inherited
    assert winacl.make_private(folder) is True
    assert winacl.make_private(folder) is False  # nothing left to change
    assert winacl.access_problem(folder) is None
    assert winacl.access_problem(folder / "inside.txt", what="file") is None


def test_a_missing_path_is_an_oserror_not_a_crash(tmp_path):
    from swarmscribe_follower import winacl

    with pytest.raises(OSError):
        winacl.read_acl(tmp_path / "absent")


def test_before_registering_a_loose_folder_of_ones_own_is_made_private(tmp_path):
    from swarmscribe_follower import winacl
    from swarmscribe_follower.credentials import CredentialStore

    folder = tmp_path / "state"
    folder.mkdir()
    let_everyone_in(folder)
    store = CredentialStore(folder / "credential.json")
    store.check_folder(tighten=False)  # doctor changes nothing and passes what run will fix
    assert winacl.access_problem(folder) is not None
    store.check_folder()  # run and join: tightened, as chmod 700 is on POSIX
    assert winacl.access_problem(folder) is None
    store.save(stored())
    assert store.load() == stored()


def test_a_credential_in_a_folder_that_was_opened_to_others_is_refused(tmp_path):
    from swarmscribe_follower.credentials import CredentialFileError, CredentialStore

    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(stored())
    let_everyone_in(tmp_path / "state")
    for check in (store.load, store.check_folder):
        with pytest.raises(CredentialFileError) as refused:
            check()
        assert "can be reached by other accounts" in str(refused.value)
        assert "secret-1" not in str(refused.value)


def test_a_folder_windows_will_not_describe_is_refused_not_a_crash(tmp_path, monkeypatch):
    from swarmscribe_follower import winacl
    from swarmscribe_follower.credentials import CredentialFileError, CredentialStore

    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(stored())

    def denied(path, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(winacl, "access_problem", denied)
    with pytest.raises(CredentialFileError, match="cannot be checked .Access is denied."):
        store.check_folder()


def test_a_credential_file_others_can_read_is_refused(tmp_path):
    from swarmscribe_follower.credentials import CredentialFileError, CredentialStore

    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(stored())
    let_everyone_in(store.path)
    with pytest.raises(CredentialFileError) as refused:
        store.load()
    assert f"the file {store.path} can be reached by other accounts" in str(refused.value)


def apply_sddl(path, sddl):
    """Replace the access list of a folder of this account's own with `sddl`, through the API."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    advapi.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    descriptor = ctypes.c_void_p()
    assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ), ctypes.WinError(ctypes.get_last_error())
    present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
    assert advapi.GetSecurityDescriptorDacl(
        descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
    )
    # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
    error = advapi.SetNamedSecurityInfoW(
        str(path), 1, 0x4 | 0x80000000, None, None, dacl, None
    )
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree(descriptor)
    assert error == 0, ctypes.WinError(error)


def test_a_new_folder_is_made_private_where_python_inherits(tmp_path, monkeypatch):
    from swarmscribe_follower import winacl
    from swarmscribe_follower.fsutil import private_folder

    parent = tmp_path / "programdata"
    parent.mkdir()
    subprocess.run(
        ["icacls", str(parent), "/grant", "*S-1-5-32-545:(OI)(CI)R"],
        check=True, capture_output=True,
    )
    real_mkdir = os.mkdir
    # Python before 3.12.4 ignores the mode on Windows: the folder inherits its parent's list.
    monkeypatch.setattr(os, "mkdir", lambda path, mode=0o777: real_mkdir(path))
    private_folder(parent / "state")
    monkeypatch.undo()
    assert winacl.access_problem(parent / "state") is None
    assert "S-1-5-32-545" not in winacl.read_acl(parent / "state")[1]


def test_an_entry_of_a_kind_that_is_not_understood_refuses_the_folder(tmp_path):
    from swarmscribe_follower import winacl

    folder = tmp_path / "state"
    folder.mkdir()
    me = winacl.current_user()
    # a conditional allow entry for Everyone (type 9): it could grant anything
    apply_sddl(folder, f"D:P(A;OICI;FA;;;{me})(XA;OICI;FA;;;WD;(Member_of{{SID(BA)}}))")
    problem = winacl.access_problem(folder)
    assert "an entry of a kind the follower does not understand" in problem
    assert f'icacls "{folder}" /inheritance:r /grant:r' in problem


def test_what_each_kind_of_entry_means():
    from swarmscribe_follower import winacl

    for kind in (1, 2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21):
        assert kind in winacl.NOT_GRANTING
    for kind in (0, 4, 5, 9, 11, 22, 99):
        assert kind not in winacl.NOT_GRANTING


def test_a_junction_is_refused_and_the_real_folder_passes(tmp_path):
    from swarmscribe_follower import winacl

    target = tmp_path / "real"
    target.mkdir()
    winacl.make_private(target)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(tmp_path / "link"), str(target)],
        check=True, capture_output=True,
    )
    assert winacl.access_problem(target) is None
    problem = winacl.access_problem(tmp_path / "link")
    assert "junction or a symbolic link" in problem
    assert "real folder" in problem


def test_a_folder_with_no_access_list_is_refused_with_the_remedy(tmp_path):
    from swarmscribe_follower import winacl

    folder = tmp_path / "state"
    folder.mkdir()
    apply_sddl(folder, "D:NO_ACCESS_CONTROL")
    assert winacl.read_acl(folder)[1] is None
    problem = winacl.access_problem(folder)
    assert "has no access control list" in problem
    assert f'icacls "{folder}" /inheritance:r /grant:r' in problem


def test_an_owner_who_is_another_principal_is_refused(tmp_path):
    # A unit-level test: ownership cannot be given away without privilege, so the owner is
    # a crafted one handed to the examination in place of what Windows says.
    from swarmscribe_follower import winacl

    users = "S-1-5-32-545"
    for what in ("folder", "file"):
        problem = winacl.access_problem(
            tmp_path / "x", what=what, acl=(users, [winacl.current_user()])
        )
        assert f"is owned by another account (Users ({users}))" in problem
