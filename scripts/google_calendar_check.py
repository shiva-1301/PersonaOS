"""Manual Google Calendar check against the RUNNING stack (real Google, your browser).

What it does:
  0. Preflight (automatic): Google is configured in the container; a forged/invalid OAuth
     state is rejected; the consent URL uses your client and the right redirect URI.
  1. Signs in your PersonaOS (Firebase) test user - password asked with a hidden prompt.
  2. If Google isn't connected yet: opens Google's consent page in your browser. Sign in
     with a Google account that is listed under "Test users" on the OAuth consent screen
     and allow calendar access. The script waits until the connection shows up.
  3. Asks the assistant to put a 30-minute test event on your calendar tomorrow at 18:00,
     shows what it proposes (nothing is created yet), then confirms with "Yes, add it".
  4. Checks the event exists in Google Calendar (via the API) and prints its link.
  5. Optional --disconnect: revokes PersonaOS's access and deletes the stored token.

Nothing secret is printed: no tokens, no client secret.

PowerShell:
  .venv\\Scripts\\python.exe scripts\\google_calendar_check.py --email <firebase-test-user-email>
  .venv\\Scripts\\python.exe scripts\\google_calendar_check.py --email <...> --disconnect
"""

import argparse
import getpass
import os
import sys
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from docker_helpers import Checks, firebase_token, http

check = Checks()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True, help="PersonaOS (Firebase) test user email")
    parser.add_argument("--disconnect", action="store_true", help="revoke access at the end")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for consent")
    args = parser.parse_args()

    password = os.environ.get("PERSONAOS_PASSWORD_A") or getpass.getpass(
        f"PersonaOS password for {args.email}: "
    )
    token = firebase_token(args.email, password)
    me = http("GET", "/me", token)[1]
    print(f"Signed in to PersonaOS as {me['email']}")

    print("\n0. Preflight")
    status = http("GET", "/integrations/google/status", token)[1]
    check(status["configured"], "Google integration is configured in the api container")
    if not status["configured"]:
        print("    Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, TOKEN_ENCRYPTION_KEY in .env, then")
        print("    docker compose up -d   (recreates the api with the new settings)")
        return check.result()
    bad = http("GET", "/integrations/google/callback?state=forged&code=x")[0]
    check(bad == 400, "a forged OAuth state is rejected")

    if not status["connected"]:
        print("\n1-2. Connect Google Calendar")
        if status["needs_reconnect"]:
            print(
                "    Your previous Google access expired (7 days in Testing mode) - reconnecting."
            )
        url = http("GET", "/integrations/google/start", token)[1]["authorization_url"]
        q = parse_qs(urlparse(url).query)
        check(
            q.get("redirect_uri") == ["http://localhost:8000/integrations/google/callback"],
            "consent URL uses the registered redirect URI",
        )
        print("    Opening Google's consent page in your browser.")
        print("    Sign in with a Google account listed under 'Test users' and allow access.")
        print("    (Testing mode shows an 'unverified app' warning: choose Continue.)")
        webbrowser.open(url)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            status = http("GET", "/integrations/google/status", token)[1]
            if status["connected"]:
                break
            time.sleep(2)
        check(status["connected"], "Google Calendar connected")
        if not status["connected"]:
            return check.result()
    else:
        print("\n1-2. Google Calendar already connected")

    print("\n3. Ask the assistant (proposal first, then confirmation)")
    when = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    title = f"PersonaOS test event {datetime.now().strftime('%H%M%S')}"
    ask = f"Put a 30-minute event called '{title}' on my calendar on {when} from 18:00 to 18:30."
    status_code, reply = http("POST", "/chat", token, {"message": ask})
    print(f"    > {ask}")
    print(f"    {reply.get('reply', reply)!r}")
    pending = reply.get("pending_confirmations", []) if status_code == 200 else []
    check(bool(pending), "the assistant proposed an event (nothing created yet)")
    if not pending:
        print("    The model didn't propose an event this time; run the script again.")
        return check.result()
    events = http("GET", "/integrations/google/events?days=3", token)[1]
    check(not any(e["title"] == title for e in events), "event NOT created before confirmation")

    status_code, reply = http("POST", "/chat", token, {"message": "Yes, add it"})
    print("    > Yes, add it")
    print(f"    {reply.get('reply', reply)!r}  tools={reply.get('tools_used')}")
    if "confirm_calendar_event" not in reply.get("tools_used", []):
        # The model didn't use the tool: confirm explicitly, like a UI button would.
        pid = pending[0]["proposal_id"]
        print("    (confirming via the API button endpoint instead)")
        http("POST", f"/integrations/google/proposals/{pid}/confirm", token)

    print("\n4. Check Google Calendar")
    events = http("GET", "/integrations/google/events?days=3", token)[1]
    created = [e for e in events if e["title"] == title]
    check(len(created) == 1, f"event exists in Google Calendar: {title}")
    if created:
        print(f"    start: {created[0]['start']}")
        print(f"    link:  {created[0]['link']}")
        print("    Open the link (or Google Calendar for tomorrow) to see it; delete it there.")

    if args.disconnect:
        print("\n5. Disconnect")
        check(
            http("DELETE", "/integrations/google", token)[0] == 204, "access revoked, token deleted"
        )
        status = http("GET", "/integrations/google/status", token)[1]
        check(not status["connected"], "status shows disconnected")
    return check.result()


if __name__ == "__main__":
    sys.exit(main())
