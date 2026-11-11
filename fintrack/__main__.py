"""Arranca a app: python -m fintrack  (abre http://localhost:8000)"""

import os

import uvicorn


def main() -> None:
    host = os.environ.get("FINTRACK_HOST", "127.0.0.1")
    port = int(os.environ.get("FINTRACK_PORT", "8000"))
    print(f"\n  FinTrack a correr em http://{host}:{port}\n")
    uvicorn.run("fintrack.web.app:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
