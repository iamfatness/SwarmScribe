"""What the Windows service control manager starts (see `windows.image`).

The service's command is the real interpreter with this file's path, not the environment's
`python.exe`: on Windows that one is a launcher which starts the interpreter as a child
process, and the control manager must talk to the process it started. Run this way, Python
knows nothing of the environment the follower is installed in, so this file puts it on the
import path first: the folder this package sits in is the environment's `site-packages`.

Standard library only, and nothing is imported before the path is right."""

import site
import sys
from pathlib import Path


def bootstrap() -> None:
    package = Path(__file__).resolve().parent
    # Python put this file's own folder first on the import path. There the follower's
    # modules (`logs`, `models`, `signals`) would be importable by their bare names.
    sys.path[:] = [entry for entry in sys.path if not entry or Path(entry).resolve() != package]
    site.addsitedir(str(package.parent))


def main() -> int:
    bootstrap()
    from swarmscribe_follower import windows

    return windows.service_process(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
