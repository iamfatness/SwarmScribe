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
    # Python makes a folder of mode 0700 with an access list of the owner, SYSTEM and
    # Administrators (3.12.4 and later); an older one inherits, and private_folder tightens.
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
