"""Block until the database accepts connections (used before migrations run)."""

import logging
import sys
import time

from app.db import database_is_reachable

TIMEOUT_SECONDS = 120
POLL_SECONDS = 2


def main() -> int:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if database_is_reachable():
            logging.info("database reachable")
            return 0
        logging.info("waiting for database")
        time.sleep(POLL_SECONDS)
    logging.error("database not reachable after %ss", TIMEOUT_SECONDS)
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
