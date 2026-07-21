"""Re-mint clasp OAuth token on this machine (Node 24 'Premature close' / 7-day
Testing-mode 'invalid_rapt' both kill the normal `clasp login`).

Reads Kurt's OWN Desktop OAuth client from ~/.clasprc.json, runs a local
listener on 53535, opens the consent URL, exchanges the code, and rewrites
~/.clasprc.json in clasp-v2 format. Then pushes Code.gs via the Apps Script REST
API (bypasses clasp/Node entirely).

Run:  python clasp_login_py.py
Then approve in the browser (Advanced -> 'Go to ... (unsafe)' on the
unverified-app warning). Permanent fix to stop the 7-day expiry: set the OAuth
consent screen to 'In production' in Google Cloud Console.
"""
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests

RC = os.path.expanduser("~/.clasprc.json")
SCRIPT_ID = "15K0MrUssFqacWybQAToz6CeHTouRU4IeNY4-DzZ4NeE1rBCCNGpGjAjv"
PORT = 53535
REDIRECT = f"http://localhost:{PORT}"
SCOPES = [
    "https://www.googleapis.com/auth/script.projects",
    "https://www.googleapis.com/auth/script.deployments",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/service.management",
    "https://www.googleapis.com/auth/logging.read",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

oc = json.load(open(RC))["oauth2ClientSettings"]
CID, CSECRET = oc["clientId"], oc["clientSecret"]
code_holder = {}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        code_holder["code"] = q.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"clasp auth done - close this tab and return to the terminal.")

    def log_message(self, *a):
        pass


def main():
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": CID, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
    })
    print("Opening browser for consent...\n", auth_url)
    webbrowser.open(auth_url)
    srv = HTTPServer(("localhost", PORT), H)
    srv.handle_request()
    code = code_holder.get("code")
    if not code:
        raise SystemExit("no code returned")
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CID, "client_secret": CSECRET, "code": code,
        "grant_type": "authorization_code", "redirect_uri": REDIRECT,
    }, timeout=30).json()
    if "refresh_token" not in tok:
        raise SystemExit("no refresh_token: " + json.dumps(tok)[:300])
    import time
    rc = {
        "token": {
            "access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
            "scope": tok.get("scope", " ".join(SCOPES)), "token_type": "Bearer",
            "expiry_date": int(time.time() * 1000) + tok.get("expires_in", 3600) * 1000,
        },
        "oauth2ClientSettings": {"clientId": CID, "clientSecret": CSECRET,
                                 "redirectUri": REDIRECT},
        "isLocalCreds": False,
    }
    json.dump(rc, open(RC, "w"))
    print("~/.clasprc.json rewritten. Pushing Code.gs via Apps Script REST...")
    push(tok["access_token"])


def push(access_token):
    here = os.path.dirname(os.path.abspath(__file__))
    files = []
    for fn, ftype in [("appsscript.json", "JSON"), ("Code.gs", "SERVER_JS")]:
        files.append({"name": fn.rsplit(".", 1)[0], "type": ftype,
                      "source": open(os.path.join(here, fn), encoding="utf-8").read()})
    r = requests.put(
        f"https://script.googleapis.com/v1/projects/{SCRIPT_ID}/content",
        headers={"Authorization": "Bearer " + access_token},
        json={"files": files}, timeout=60)
    print("push", r.status_code, [f["name"] for f in r.json().get("files", [])] if r.ok else r.text[:400])


if __name__ == "__main__":
    main()
