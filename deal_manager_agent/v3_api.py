"""Server-to-server CRM deal edits through the v3 API, as Sasha v3 `V3Service.edit_deal` does.

Auth is a short-lived HS256 JWT `{purpose: "authentication", userId}` signed with
JWT_AUTH_KEY for SASHA_ADMIN_USER, passed as `authToken` (v3 `resolveAuthToken`).
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

EDIT_DEAL_PATH = "/api/v4/sales-pipeline/deals/edit-deal-card"
ASSIGN_MANAGER_PATH = "/api/v4/user/assign-manager"
TOKEN_TTL_SECONDS = 600


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_token(secret: str, user_id: int, now: float | None = None) -> str:
    header = _base64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"purpose": "authentication", "userId": user_id,
              "exp": int((now or time.time()) + TOKEN_TTL_SECONDS)}
    payload = _base64url(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_base64url(signature)}"


def _error_message(raw, status: int) -> str:
    if isinstance(raw, dict):
        if raw.get("message"):
            return str(raw["message"])
        error = raw.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if error is not None:
            return str(error)
    return f"Request failed with status {status}"


class V3Api:
    def __init__(self, base_url: str, jwt_key: str, admin_user_id: int, timeout: float = 45,
                 opener=urllib.request.urlopen):
        self.base_url = base_url.rstrip("/")
        self.jwt_key = jwt_key
        self.admin_user_id = admin_user_id
        self.timeout = timeout
        self.opener = opener

    @classmethod
    def from_environment(cls) -> "V3Api | None":
        """None when CRM writes are not configured (V3_API_BASE_URL, JWT_AUTH_KEY, SASHA_ADMIN_USER)."""
        base_url = os.environ.get("V3_API_BASE_URL", "").strip()
        jwt_key = os.environ.get("JWT_AUTH_KEY", "").strip()
        admin_user = os.environ.get("SASHA_ADMIN_USER", "").strip()
        if not (base_url and jwt_key and admin_user.isdigit() and int(admin_user) > 0):
            return None
        return cls(base_url, jwt_key, int(admin_user),
                   float(os.environ.get("V3_API_TIMEOUT_SECONDS") or 45))

    def edit_deal(self, deal_id: int, payload: dict) -> tuple[bool, str]:
        """PUT the edit-deal card. Returns (success, message); never raises for HTTP errors."""
        return self._put(EDIT_DEAL_PATH, {**payload, "id": deal_id}, "Deal updated successfully")

    def assign_manager(self, client_id: int, manager_id: int, client_email: str) -> tuple[bool, str]:
        """Make the ISA who accepted a handoff the client's manager (v3 call_assign_manager_api)."""
        return self._put(ASSIGN_MANAGER_PATH, {"clientId": client_id, "managerId": manager_id,
                                               "clientEmail": (client_email or "").strip()},
                         "Manager assigned")

    def _put(self, path: str, payload: dict, success_message: str) -> tuple[bool, str]:
        token = sign_token(self.jwt_key, self.admin_user_id)
        url = f"{self.base_url}{path}?{urllib.parse.urlencode({'authToken': token})}"
        body = json.dumps(payload).encode()
        request = urllib.request.Request(url, data=body, method="PUT",
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = self._json(response.read())
                message = raw.get("message") if isinstance(raw, dict) else None
                return True, str(message or success_message)
        except urllib.error.HTTPError as error:
            with error:
                return False, _error_message(self._json(error.read()), error.code)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # The URL carries the token; report only the failure kind.
            return False, f"v3 API request failed ({type(error).__name__})"

    @staticmethod
    def _json(data: bytes):
        try:
            return json.loads(data or b"{}")
        except ValueError:
            return {}
