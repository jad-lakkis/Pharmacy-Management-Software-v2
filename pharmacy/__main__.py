"""Run the app: ``python -m pharmacy [--port 8000] [--db data/pharmacy.db] [--memory] [--reset]``."""

import argparse
import logging
from pathlib import Path

from .api import Api, serve
from .repositories import Repositories
from .seed import DEMO_ACCOUNTS, seed
from .services import PharmacyApp


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pharmacy", description="Pharmacy Management Software")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="data/pharmacy.db", help="SQLite database file")
    parser.add_argument("--memory", action="store_true", help="use a throwaway in-memory database")
    parser.add_argument("--reset", action="store_true", help="delete the database file and reseed")
    parser.add_argument("--no-seed", action="store_true", help="do not load demo data into an empty database")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.memory:
        repos = Repositories.in_memory()
    else:
        db = Path(args.db)
        if args.reset:
            for suffix in ("", "-wal", "-shm"):
                Path(str(db) + suffix).unlink(missing_ok=True)
        repos = Repositories.sqlite(db)
    if not args.no_seed and repos.is_empty():
        seed(repos)
        logging.info("Loaded demo data. Demo logins: %s",
                     ", ".join(f"{u} / {pw}" for u, (pw, _, _) in DEMO_ACCOUNTS.items()))

    # Offer one-click demo sign-in only while the demo accounts still exist.
    demo = [
        {"username": u, "password": pw, "label": name}
        for u, (pw, _, name) in DEMO_ACCOUNTS.items()
        if repos.users.by_username(u) is not None
    ] if not args.no_seed else []
    app = PharmacyApp(repos)
    httpd = serve(Api(app, demo), args.host, args.port)
    logging.info("Serving on http://%s:%d", args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        repos.close()


if __name__ == "__main__":
    main()
