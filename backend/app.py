import os
import hmac
import json
import time
import hashlib
import threading
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

TUYA_BASE = os.getenv("TUYA_BASE_URL", "https://openapi.tuyacn.com")
TUYA_CLIENT_ID = os.getenv("TUYA_CLIENT_ID", "")
TUYA_CLIENT_SECRET = os.getenv("TUYA_CLIENT_SECRET", "")
TUYA_DEVICE_ID = os.getenv("TUYA_DEVICE_ID", "")
TUYA_REMOTE_ID = os.getenv("TUYA_REMOTE_ID", "")
TUYA_SEND_MODE = os.getenv("TUYA_SEND_MODE", "remote_api").strip().lower()  # remote_api | dp_raw
API_BEARER = os.getenv("API_BEARER", "")
COMMAND_MAP_JSON = os.getenv("COMMAND_MAP_JSON", "{}")
RAW_CODE_MAP_JSON = os.getenv("RAW_CODE_MAP_JSON", "{}")
RAW_CATEGORY_ID = int(os.getenv("RAW_CATEGORY_ID", "13") or "13")
UI_STATE_FILE = os.getenv("UI_STATE_FILE", "/data/ui_state.json")

try:
    COMMAND_MAP = json.loads(COMMAND_MAP_JSON)
except Exception:
    COMMAND_MAP = {}

try:
    RAW_CODE_MAP = json.loads(RAW_CODE_MAP_JSON)
except Exception:
    RAW_CODE_MAP = {}

_token_cache = {"token": "", "exp": 0}
_ui_state_lock = threading.Lock()
_http = requests.Session()
_adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
_http.mount("https://", _adapter)
_http.mount("http://", _adapter)


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _string_to_sign(method: str, path: str, query: dict | None, body_obj: dict | None) -> str:
    query = query or {}
    body_obj = body_obj or {}
    body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False) if body_obj else ""
    content_hash = _sha256_hex(body)
    url = path
    if query:
        url = f"{path}?{urlencode(query)}"
    return "\n".join([method.upper(), content_hash, "", url])


def _sign(message: str) -> str:
    return hmac.new(TUYA_CLIENT_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest().upper()


def _tuya_request(method: str, path: str, *, query: dict | None = None, body: dict | None = None, access_token: str = ""):
    t = str(int(time.time() * 1000))
    string_to_sign = _string_to_sign(method, path, query, body)
    body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""

    if access_token:
        sign_payload = f"{TUYA_CLIENT_ID}{access_token}{t}{string_to_sign}"
    else:
        sign_payload = f"{TUYA_CLIENT_ID}{t}{string_to_sign}"

    headers = {
        "client_id": TUYA_CLIENT_ID,
        "t": t,
        "sign_method": "HMAC-SHA256",
        "sign": _sign(sign_payload),
        "Content-Type": "application/json",
    }
    if access_token:
        headers["access_token"] = access_token

    url = f"{TUYA_BASE}{path}"
    resp = _http.request(
        method=method.upper(),
        url=url,
        params=query,
        data=body_text if body is not None else None,
        headers=headers,
        timeout=20,
    )
    return resp.status_code, resp.text


def _get_token() -> str:
    now = int(time.time())
    if _token_cache["token"] and _token_cache["exp"] > now + 30:
        return _token_cache["token"]

    code, text = _tuya_request("GET", "/v1.0/token", query={"grant_type": 1})
    if code // 100 != 2:
        raise RuntimeError(f"token http {code}: {text[:300]}")
    data = json.loads(text)
    if not data.get("success"):
        raise RuntimeError(f"token failed: {data}")

    token = data["result"]["access_token"]
    expire_time = int(data["result"].get("expire_time", 7200))
    _token_cache["token"] = token
    _token_cache["exp"] = int(time.time()) + expire_time
    return token


def _check_config():
    miss = []
    base_need = {
        "TUYA_CLIENT_ID": TUYA_CLIENT_ID,
        "TUYA_CLIENT_SECRET": TUYA_CLIENT_SECRET,
        "TUYA_DEVICE_ID": TUYA_DEVICE_ID,
    }
    if TUYA_SEND_MODE == "remote_api":
        base_need["TUYA_REMOTE_ID"] = TUYA_REMOTE_ID
    for k, v in base_need.items():
        if not v:
            miss.append(k)
    return miss


def _default_ui_state():
    return {
        "active_preset": "",
        "preset_labels": {},
        "updated_at": int(time.time()),
    }


def _sanitize_ui_state(data):
    state = _default_ui_state()
    if isinstance(data, dict):
        active_preset = str(data.get("active_preset", "")).strip()
        state["active_preset"] = active_preset if active_preset in {str(i) for i in range(1, 10)} else ""

        labels = data.get("preset_labels", {})
        clean_labels = {}
        if isinstance(labels, dict):
            for i in range(1, 10):
                key = str(i)
                value = str(labels.get(key, "")).strip()
                if value:
                    clean_labels[key] = value[:80]
        state["preset_labels"] = clean_labels

        try:
            state["updated_at"] = int(data.get("updated_at", state["updated_at"]))
        except Exception:
            pass
    return state


def _ensure_ui_state_dir():
    os.makedirs(os.path.dirname(UI_STATE_FILE), exist_ok=True)


def _read_ui_state():
    with _ui_state_lock:
        try:
            with open(UI_STATE_FILE, "r", encoding="utf-8") as f:
                return _sanitize_ui_state(json.load(f))
        except FileNotFoundError:
            state = _default_ui_state()
            _write_ui_state(state)
            return state
        except Exception:
            return _default_ui_state()


def _write_ui_state(state):
    clean_state = _sanitize_ui_state(state)
    clean_state["updated_at"] = int(time.time())
    _ensure_ui_state_dir()
    tmp_path = f"{UI_STATE_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(clean_state, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, UI_STATE_FILE)
    return clean_state


def _patch_ui_state(patch):
    with _ui_state_lock:
        try:
            with open(UI_STATE_FILE, "r", encoding="utf-8") as f:
                current = _sanitize_ui_state(json.load(f))
        except Exception:
            current = _default_ui_state()

        if "active_preset" in patch:
            active_preset = str(patch.get("active_preset", "")).strip()
            current["active_preset"] = active_preset if active_preset in {str(i) for i in range(1, 10)} else ""

        if "preset_labels" in patch:
            labels = patch.get("preset_labels", {})
            if isinstance(labels, dict):
                clean_labels = {}
                for i in range(1, 10):
                    key = str(i)
                    value = str(labels.get(key, "")).strip()
                    if value:
                        clean_labels[key] = value[:80]
                current["preset_labels"] = clean_labels

        return _write_ui_state(current)


def _auth_guard():
    if API_BEARER:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_BEARER}":
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


def _send_remote_api(token: str, raw_key: str):
    path = f"/v1.0/infrareds/{TUYA_DEVICE_ID}/remotes/{TUYA_REMOTE_ID}/raw/command"
    body = {"raw_key": raw_key, "category_id": RAW_CATEGORY_ID}
    return _tuya_request("POST", path, body=body, access_token=token)


def _send_dp_raw(token: str, raw_code: str):
    path = f"/v1.0/iot-03/devices/{TUYA_DEVICE_ID}/commands"
    body = {
        "commands": [
            {"code": "key_study", "value": raw_code},
            {"code": "control", "value": "study_key"},
        ]
    }
    return _tuya_request("POST", path, body=body, access_token=token)


def _read_latest_study_code(token: str):
    path = f"/v2.0/cloud/thing/{TUYA_DEVICE_ID}/shadow/properties"
    code, text = _tuya_request("GET", path, access_token=token)
    data = json.loads(text) if text else {}
    if code // 100 != 2 or not data.get("success"):
        return "", code, data
    for p in data.get("result", {}).get("properties", []):
        if p.get("code") == "study_code":
            return p.get("value") or "", code, data
    return "", code, data


@app.get("/health")
def health():
    miss = _check_config()
    return jsonify({
        "ok": len(miss) == 0,
        "missing": miss,
        "tuya_base": TUYA_BASE,
        "send_mode": TUYA_SEND_MODE,
        "mapped_keys": sorted(COMMAND_MAP.keys()),
        "raw_mapped_keys": sorted(RAW_CODE_MAP.keys()),
    })


@app.get("/api/ui-state")
def get_ui_state():
    auth_fail = _auth_guard()
    if auth_fail:
        return auth_fail
    return jsonify({"ok": True, "state": _read_ui_state()})


@app.post("/api/ui-state")
def update_ui_state():
    auth_fail = _auth_guard()
    if auth_fail:
        return auth_fail

    payload = request.get_json(silent=True) or {}
    try:
        state = _patch_ui_state(payload)
        return jsonify({"ok": True, "state": state})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/control")
def control():
    auth_fail = _auth_guard()
    if auth_fail:
        return auth_fail

    miss = _check_config()
    if miss:
        return jsonify({"ok": False, "error": "missing config", "missing": miss}), 400

    payload = request.get_json(silent=True) or {}
    key = str(payload.get("key", "")).strip()
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400

    try:
        token = _get_token()
        if TUYA_SEND_MODE == "dp_raw":
            raw_code = str(RAW_CODE_MAP.get(key, "")).strip()
            if not raw_code:
                return jsonify({
                    "ok": False,
                    "error": "raw code missing",
                    "hint": "Set RAW_CODE_MAP_JSON, for example: {\"up\":\"<study_code>\"}",
                    "key": key,
                }), 400
            code, text = _send_dp_raw(token, raw_code)
        else:
            raw_key = str(COMMAND_MAP.get(key, key)).strip()
            code, text = _send_remote_api(token, raw_key)

        data = json.loads(text) if text else {}
        if code // 100 == 2 and data.get("success"):
            return jsonify({"ok": True, "key": key, "mode": TUYA_SEND_MODE})
        return jsonify({"ok": False, "key": key, "mode": TUYA_SEND_MODE, "status": code, "resp": data}), 502
    except Exception as e:
        return jsonify({"ok": False, "key": key, "mode": TUYA_SEND_MODE, "error": str(e)}), 500


@app.post("/api/learn/start")
def learn_start():
    auth_fail = _auth_guard()
    if auth_fail:
        return auth_fail
    token = _get_token()
    path = f"/v1.0/iot-03/devices/{TUYA_DEVICE_ID}/commands"
    body = {"commands": [{"code": "control", "value": "study"}]}
    code, text = _tuya_request("POST", path, body=body, access_token=token)
    data = json.loads(text) if text else {}
    return jsonify({"ok": code // 100 == 2 and data.get("success"), "status": code, "resp": data})


@app.post("/api/learn/stop")
def learn_stop():
    auth_fail = _auth_guard()
    if auth_fail:
        return auth_fail
    token = _get_token()
    path = f"/v1.0/iot-03/devices/{TUYA_DEVICE_ID}/commands"
    body = {"commands": [{"code": "control", "value": "study_exit"}]}
    code, text = _tuya_request("POST", path, body=body, access_token=token)
    data = json.loads(text) if text else {}
    return jsonify({"ok": code // 100 == 2 and data.get("success"), "status": code, "resp": data})


@app.get("/api/learn/last")
def learn_last():
    auth_fail = _auth_guard()
    if auth_fail:
        return auth_fail
    token = _get_token()
    study_code, status, raw_data = _read_latest_study_code(token)
    return jsonify({"ok": bool(study_code), "status": status, "study_code": study_code, "raw": raw_data})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
