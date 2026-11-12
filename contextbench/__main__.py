import os
import sys

if len(sys.argv) > 1 and sys.argv[1] == "run" and os.environ.get("PYTHONHASHSEED") != "0":
    # Some engines break a tie in set order. A fixed hash seed makes a run repeat, so a run restarts itself with it.
    os.execve(sys.executable, [sys.executable, "-m", "contextbench"] + sys.argv[1:],
              dict(os.environ, PYTHONHASHSEED="0"))

from contextbench.cli import main  # noqa: E402

raise SystemExit(main())
