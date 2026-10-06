"""Windows: who may reach the state folder and the credential file (follower spec 5.3).

POSIX mode bits mean nothing on NTFS, so on Windows the credential's trust check reads the
access control list instead. The rule is the POSIX one in Windows terms: the folder and the
file must belong to this account (or to SYSTEM or the Administrators group, which own what an
installer creates), and nobody but this account, SYSTEM and Administrators may be allowed in,
for anything. A folder under the user's profile (the default state folder) and one made by
`swarmscribe-follower service install` pass; a folder made at the root of a drive does not
(there `Authenticated Users` may write).

Only plain allow entries (type 0) name who is let in. The entry types that can only take
access away or record something (deny, audit, alarm, labels, policy: NOT_GRANTING) are
ignored. Any other kind could grant access in a way this code does not read (a conditional or
an object entry), so the check refuses a list that holds one rather than guess.

A state folder that is itself a junction or a symbolic link is refused: its list is read by
path, which would follow the link, as a separate step from the later opens. A link higher up
in the path is not checked.

One principal is added, and only for an elevated administrator: the follower's own service
account (the virtual account "NT SERVICE" slash "SwarmScribeFollower"). The service's
folder and credential belong to it, and `leave` or `doctor` run from an elevated prompt must
be able to read them. Nobody else is widened: a process that is not an elevated
administrator refuses what that account owns or can reach, as it refuses any other account's.

Every function here raises OSError when Windows refuses a call, and is called on Windows only."""

import ctypes
import functools
import hashlib
import os
import struct
from pathlib import Path

from .windows import SERVICE_NAME  # the one definition; windows.py imports only the stdlib

SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
# CREATOR OWNER and OWNER RIGHTS stand for whoever owns the object: the owner is checked.
OWNER_PLACEHOLDERS = frozenset({"S-1-3-0", "S-1-3-4"})
KNOWN = {
    "S-1-1-0": "Everyone",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "Users",
    "S-1-5-4": "Interactive",
    SYSTEM: "SYSTEM",
    ADMINISTRATORS: "Administrators",
}
_SE_FILE_OBJECT = 1
_DACL = 0x4
_OWNER_AND_DACL = 0x1 | _DACL
_PROTECTED_DACL = 0x80000000
_ACCESS_ALLOWED_ACE = 0
_INHERIT_ONLY = 0x8  # ACE flag: for what is made inside, not for the object
# ACE types (winnt.h) known not to grant access: deny, audit, alarm, their object and callback
# variants, mandatory label, resource attribute, scoped policy, process trust label and access
# filter. Allow (0), compound allow (4), allow object (5), allow callback (9) and allow
# callback object (11) can grant; a type not listed at all is unknown. Both are refused
# unless plain allow.
NOT_GRANTING = frozenset({1, 2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21})
UNREADABLE = "?"  # marks, in the list read_acl returns, an entry that cannot be read
_REPARSE_POINT = 0x400
_TOKEN_QUERY = 0x8
_TOKEN_USER = 1


@functools.cache
def _api():
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(ctypes.c_void_p)
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer, pointer,
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer, pointer,
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetAclInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
    ]
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, pointer]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return advapi, kernel


def _sid_text(advapi, kernel, sid: int) -> str:
    from ctypes import wintypes

    text = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value or ""
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def service_sid(name: str) -> str:
    r"""The SID Windows gives the service `name` (its virtual account `NT SERVICE\name`):
    S-1-5-80 and the five 32-bit words of the SHA-1 of the upper-cased name in UTF-16LE.
    Pure: it needs no Windows call."""
    digest = hashlib.sha1(name.upper().encode("utf-16-le")).digest()
    return "S-1-5-80-" + "-".join(str(word) for word in struct.unpack("<5I", digest))


def is_administrator() -> bool:
    """True when this process is an administrator with an elevated token (`IsUserAnAdmin`
    is false for an administrator whose token is filtered by User Account Control)."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def trusted() -> set[str]:
    """The principals this process lets into a folder that holds the credential, for the
    check (`access_problem`) and for making private (`make_private`) alike: this account,
    SYSTEM, Administrators, and, for an elevated administrator only, the follower's own
    service account (so `leave` and `doctor` can read the service's folder, and nothing
    rewrites its list)."""
    found = {current_user(), SYSTEM, ADMINISTRATORS}
    if is_administrator():
        found.add(service_sid(SERVICE_NAME))
    return found


def _own_owners() -> set[str]:
    """Who may own what this account made. An elevated administrator's files belong to the
    Administrators group, not to the user."""
    return {current_user()} | ({ADMINISTRATORS} if is_administrator() else set())


@functools.cache
def current_user() -> str:
    """The SID of the account this process runs as, as text (`S-1-5-21-...`)."""
    from ctypes import wintypes

    advapi, kernel = _api()
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(
            token, _TOKEN_USER, buffer, size.value, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER starts with a pointer to the SID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        return _sid_text(advapi, kernel, sid)
    finally:
        kernel.CloseHandle(token)


def _read(
    path: Path, descriptor_of: int | None = None
) -> tuple[str, list[tuple[str, int, int]] | None]:
    """(the owner's SID, the entries that can let someone in) for a file or folder; each
    entry is (SID, access mask, entry flags). An entry of a kind this code does not read is
    (UNREADABLE + its type, every bit, its flags). The list is None when the object has no
    access control list at all."""
    import msvcrt
    from ctypes import wintypes

    advapi, kernel = _api()
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    out = (ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if descriptor_of is None:
        error = advapi.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _OWNER_AND_DACL, *out)
    else:
        handle = msvcrt.get_osfhandle(descriptor_of)
        error = advapi.GetSecurityInfo(handle, _SE_FILE_OBJECT, _OWNER_AND_DACL, *out)
    if error:
        raise ctypes.WinError(error)
    try:
        owner_sid = _sid_text(advapi, kernel, owner.value)
        if not dacl.value:
            return owner_sid, None
        counts = (wintypes.DWORD * 3)()  # ACL_SIZE_INFORMATION: the entry count comes first
        if not advapi.GetAclInformation(dacl, counts, ctypes.sizeof(counts), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        entries = []
        for index in range(counts[0]):
            entry = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            # ACE_HEADER is a type byte, a flags byte and a 16-bit size; an allow entry
            # follows it with a 32-bit mask and then the SID.
            header = ctypes.cast(entry, ctypes.POINTER(ctypes.c_ubyte))
            kind, flags = header[0], header[1]
            mask = ctypes.cast(entry.value + 4, ctypes.POINTER(wintypes.DWORD))[0]
            if kind == _ACCESS_ALLOWED_ACE:
                if mask:
                    entries.append((_sid_text(advapi, kernel, entry.value + 8), mask, flags))
            elif kind not in NOT_GRANTING:
                entries.append((f"{UNREADABLE}{kind}", 0xFFFFFFFF, flags))
        return owner_sid, entries
    finally:
        kernel.LocalFree(descriptor)


def read_acl(path: Path, descriptor_of: int | None = None) -> tuple[str, list[str] | None]:
    """(the owner's SID, the SIDs with an allow entry) for a file or folder. The list is None
    when the object has no access control list at all: then everyone may do anything.

    `descriptor_of`: an open file descriptor of `path`. The file is then read through it and
    not opened a second time: the answer is about the file that is being read, and another
    writer's replace of the credential is not held up by a second open."""
    owner, entries = _read(path, descriptor_of)
    return owner, None if entries is None else [sid for sid, _mask, _flags in entries]


def read_grants(path: Path) -> tuple[str, list[tuple[str, int]] | None]:
    """(the owner's SID, (SID, access mask) of every allow entry that applies to `path`
    itself): what each principal may do with it, for `windows.image_problem`. An entry that is
    only handed on to what is made inside a folder (inherit-only) is left out. The list is
    None when there is no access control list at all."""
    owner, entries = _read(path)
    if entries is None:
        return owner, None
    return owner, [(sid, mask) for sid, mask, flags in entries if not flags & _INHERIT_ONLY]


def _name(sid: str) -> str:
    return f"{KNOWN[sid]} ({sid})" if sid in KNOWN else sid


def access_problem(
    path: Path,
    *,
    what: str = "folder",
    owner_only: bool = False,
    acl: tuple[str, list[str] | None] | None = None,
) -> str | None:
    """None when `path` can be trusted with the credential; else why not, naming who else is
    allowed in and the command that makes it private. `owner_only`: look at the owner alone
    (`doctor` on a folder that `run` would make private before it uses it). `acl`: what
    `read_acl` already said of `path` (the credential file, read while it was open)."""
    me = current_user()
    if acl is None and os.lstat(path).st_file_attributes & _REPARSE_POINT:
        return (
            f"the {what} {path} is a junction or a symbolic link and will not be trusted with"
            " the credential; point the setting at the real folder"
        )
    owner, allowed = acl if acl is not None else read_acl(path)
    allowed_in = trusted()
    fix = (
        f'icacls "{path}" /inheritance:r /grant:r "*{me}:(OI)(CI)F" "*{SYSTEM}:(OI)(CI)F"'
        f' "*{ADMINISTRATORS}:(OI)(CI)F"'
        if what == "folder"
        else f'icacls "{path}" /inheritance:r /grant:r "*{me}:F" "*{SYSTEM}:F"'
        f' "*{ADMINISTRATORS}:F"'
    )
    if owner not in allowed_in:
        return (
            f"the {what} {path} is owned by another account ({_name(owner)}) and will not be"
            " trusted with the credential; use a folder of this account's own (the default is"
            r" under %LOCALAPPDATA%)"
        )
    if owner_only:
        return None
    if allowed is None:
        return (
            f"the {what} {path} has no access control list (everyone may do anything) and will"
            f" not be trusted with the credential; run: {fix}"
        )
    unknown = sorted(entry for entry in allowed if entry.startswith(UNREADABLE))
    if unknown:
        return (
            f"the {what} {path} has an access list with an entry of a kind the follower does"
            f" not understand (type {', '.join(entry[1:] for entry in unknown)}) and will not"
            f" be trusted with the credential; make it private with: {fix}"
        )
    others = sorted({sid for sid in allowed if sid not in allowed_in | OWNER_PLACEHOLDERS})
    if others:
        removes = " ".join(f'/remove "*{sid}"' for sid in others)
        return (
            f"the {what} {path} can be reached by other accounts"
            f" ({', '.join(_name(sid) for sid in others)}) and will not be trusted with the"
            f" credential; make it private with: {fix} {removes}"
        )
    return None


def make_private(path: Path) -> bool:
    """What `chmod 700` is on POSIX: when the folder `path` is this account's own and others
    are allowed in, replace its access control list with one that names this account, SYSTEM
    and Administrators only, inherited by everything inside it, and inheriting nothing from
    above. True when it was changed. A folder that is someone else's is left alone (the
    callers that trust a folder refuse it by themselves)."""
    from ctypes import wintypes

    me = current_user()
    if os.lstat(path).st_file_attributes & _REPARSE_POINT:
        return False  # a link: not followed, and refused by the check
    owner, allowed = read_acl(path)
    if owner not in _own_owners():
        return False
    if allowed is not None and set(allowed) <= trusted() | OWNER_PLACEHOLDERS:
        return False
    advapi, kernel = _api()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
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
    # D:P = a protected list (nothing inherited); FA = full access; OICI = files and folders
    # inside inherit it; SY and BA are SYSTEM and Administrators.
    text = f"D:P(A;OICI;FA;;;{me})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    descriptor = ctypes.c_void_p()
    if not convert(text, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        error = advapi.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT, _DACL | _PROTECTED_DACL, None, None, dacl, None
        )
        if error:
            raise ctypes.WinError(error)
    finally:
        kernel.LocalFree(descriptor)
    return True


# Protected (nothing inherited), full control for SYSTEM and Administrators, inherited by
# everything made inside: the data folder of the service, from the instant it exists.
PROTECTED_ROOT_SDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
_ERROR_ALREADY_EXISTS = 183


def create_protected_directory(path: Path, sddl: str = PROTECTED_ROOT_SDDL) -> bool:
    """Make the folder `path` (its parent must exist) with the list `sddl` set by the
    creating call itself, so there is no moment at which it carries the parent's inherited
    list. True when it was made; False when it was already there (the caller decides what to
    do about a folder it did not make). Raises OSError when Windows refuses."""
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("descriptor", ctypes.c_void_p),
            ("inherit", wintypes.BOOL),
        ]

    advapi, kernel = _api()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    convert.restype = wintypes.BOOL
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        made = kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))
        error = ctypes.get_last_error()
    finally:
        kernel.LocalFree(descriptor)
    if made:
        return True
    if error == _ERROR_ALREADY_EXISTS:
        return False
    raise ctypes.WinError(error)
