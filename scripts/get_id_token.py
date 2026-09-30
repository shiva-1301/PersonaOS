"""Get a Firebase ID token for a test user (email/password) and print it to stdout.

Uses the Identity Toolkit REST endpoint accounts:signInWithPassword with the web API key
from .env (FIREBASE_WEB_API_KEY). The password is read with a hidden prompt, never echoed.

PowerShell:
    $t = .venv\\Scripts\\python.exe scripts\\get_id_token.py --email test-a@example.com
    curl.exe -H "Authorization: Bearer $t" http://localhost:8000/me
"""

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    env = {**dotenv_values(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
    api_key = env.get("FIREBASE_WEB_API_KEY")
    if not api_key:
        print("FIREBASE_WEB_API_KEY is not set in .env", file=sys.stderr)
        return 2

    password = getpass.getpass(f"Password for {args.email}: ")
    body = json.dumps(
        {"email": args.email, "password": password, "returnSecureToken": True}
    ).encode()
    req = urllib.request.Request(
        SIGN_IN_URL.format(key=api_key),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read())["error"]["message"]
        except Exception:
            message = f"HTTP {exc.code}"
        print(f"Sign-in failed: {message}", file=sys.stderr)
        return 1

    print(f"Signed in as uid {data['localId']}; token valid {data['expiresIn']}s", file=sys.stderr)
    print(data["idToken"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
