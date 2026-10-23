"""Start the public demo inside a CodeSandbox Python Devbox."""

import os
import sys
from pathlib import Path


def main():
    import uvicorn

    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    port = int(os.environ.get("PORT", "8000"))
    os.environ.setdefault("GOULASH_PUBLIC_DEMO", "1")
    os.environ.setdefault("GOULASH_DB", "data/codesandbox/base.db")
    os.environ.setdefault("GOULASH_SESSIONS_DIR", "data/codesandbox/sessions")
    preview_host = os.environ.get("CODESANDBOX_HOST", "")
    if preview_host and not os.environ.get("GOULASH_PUBLIC_ORIGIN"):
        preview_host = preview_host.replace("${PORT}", str(port)).replace("$PORT", str(port))
        os.environ["GOULASH_PUBLIC_ORIGIN"] = f"https://{preview_host}"
    if os.environ.get("GOULASH_PUBLIC_ORIGIN"):
        print("Public preview:", os.environ["GOULASH_PUBLIC_ORIGIN"], flush=True)
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, proxy_headers=True)


if __name__ == "__main__":
    main()
