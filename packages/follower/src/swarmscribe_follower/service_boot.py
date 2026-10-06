"""What the Windows service control manager starts (see `windows.image`).

The service's command is the real interpreter with this file's path, not the environment's
`python.exe`: on Windows that one is a launcher which starts the interpreter as a child
process, and the control manager must talk to the process it started. Run this way, Python
knows nothing of the environment the follower is installed in, so this file puts it on the
import path: the folder this package sits in is the environment's `site-packages`. It goes
FIRST, with whatever its `.pth` files add, ahead of the interpreter's own `site-packages`:
a package installed for the whole machine must not shadow one of the follower's. (The
registered command also starts the interpreter with `-I`, so `PYTHONPATH` and the user's
site-packages are not there at all; this file does not rely on it.)

Standard library only, and nothing is imported before the path is right."""

import os
import site
import sys
from pathlib import Path


def bootstrap() -> None:
    package = Path(__file__).resolve().parent
    home = str(package.parent)

    def same(entry: str, folder: str) -> bool:
        return bool(entry) and os.path.normcase(os.path.abspath(entry)) == os.path.normcase(folder)

    # Without `-I` Python puts this file's own folder first on the import path. There the
    # follower's modules (`logs`, `models`, `signals`) would be importable by their bare names.
    before = [
        entry for entry in sys.path if not same(entry, str(package)) and not same(entry, home)
    ]
    sys.path[:] = before
    site.addsitedir(home)  # appends the folder and what its .pth files name
    added = [entry for entry in sys.path if entry not in before]
    sys.path[:] = added + before


def main() -> int:
    bootstrap()
    from swarmscribe_follower import windows

    return windows.service_process(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
