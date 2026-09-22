"""Hold an OS lock for the lifetime of the business process, across exec()."""

import fcntl
import hashlib
import os
from pathlib import Path

key = os.environ["WORKBENCH_DEPLOYMENT_KEY"]
locks = Path("/workspace/.workbench/locks")
locks.mkdir(parents=True, exist_ok=True)
lock = (locks / hashlib.sha256(key.encode()).hexdigest()).open("a")
# A cancelled Prefect state alone is not proof that the previous process exited.
fcntl.flock(lock, fcntl.LOCK_EX)
os.set_inheritable(lock.fileno(), True)
os.execv("/opt/workbench/bin/python", ["python", "-m", "prefect.engine"])
