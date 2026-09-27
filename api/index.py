# Rehan Codex — Free Fire Like API
# Vercel serverless build, Python 3.11

from flask import Flask, request, jsonify
import asyncio
import os
import json
import binascii
import requests
import aiohttp
from collections import OrderedDict
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.message import DecodeError

import like_pb2
import uid_generator_pb2
import visit_count_pb2

app = Flask(__name__)

# ---------- Config ----------
VALID_API_KEYS = {
    "RehanCodex",
    "rehan",
}
daily_limit = 20
used_count = 0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV  = b'6oyZDr22E3ychjM%'


def _json_path(name):
    for base in (BASE_DIR, ROOT_DIR, os.getcwd()):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(ROOT_DIR, name)


def load_tokens(region):
    try:
        if region == "IND":
            fname = "token_ind.json"
        elif region in {"BR", "US", "SAC", "NA"}:
            fname = "token_br.json"
        else:
            fname = "token_bd.json"
        path = _json_path(fname)
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"load_tokens error region={region}: {e}")
        return None


def encrypt_message(plaintext):
    try:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        padded = pad(plaintext, AES.block_size)
        encrypted = cipher.encrypt(padded)
        return binascii.hexlify(encrypted).decode("utf-8")
    except Exception as e:
        app.logger.error(f"encrypt_message error: {e}")
        return None


def create_like_protobuf(uid, region):
    try:
        msg = like_pb2.like()
        msg.uid = int(uid)
        msg.region = region
        return msg.SerializeToString()
    except Exception as e:
        app.logger.error(f"create_like_protobuf error: {e}")
        return None


def create_uid_protobuf(uid):
    try:
        msg = uid_generator_pb2.uid_generator()
        msg.saturn_ = int(uid)
        msg.garena = 1
        return msg.SerializeToString()
    except Exception as e:
        app.logger.error(f"create_uid_protobuf error: {e}")
        return None


def enc(uid):
    pb = create_uid_protobuf(uid)
    if pb is None:
        return None
    return encrypt_message(pb)


def region_host(region):
    if region == "IND":
        return "https://client.ind.freefiremobile.com"
    if region in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    return "https://clientbp.ggpolarbear.com"


def base_headers(token):
    return {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": "OB54",
    }


async def _post_like(session, edata, token, url):
    try:
        async with session.post(url, data=edata, headers=base_headers(token)) as r:
            return await r.text()
    except Exception as e:
        app.logger.error(f"_post_like error: {e}")
        return None


async def send_multiple_requests(uid, region, url):
    try:
        pb = create_like_protobuf(uid, region)
        if pb is None:
            return None
        encrypted_uid = encrypt_message(pb)
        if encrypted_uid is None:
            return None
        tokens = load_tokens(region)
        if not tokens:
            return None
        edata = bytes.fromhex(encrypted_uid)

        tasks = []
        async with aiohttp.ClientSession() as session:
            for i in range(100):
                token = tokens[i % len(tokens)]["token"]
                tasks.append(_post_like(session, edata, token, url))
            return await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        app.logger.error(f"send_multiple_requests error: {e}")
        return None


def fetch_player(encrypted_uid, region, token):
    try:
        url = region_host(region) + "/GetPlayerPersonalShow"
        edata = bytes.fromhex(encrypted_uid)
        r = requests.post(url, data=edata, headers=base_headers(token), verify=False, timeout=15)
        decoded = visit_count_pb2.Info()
        decoded.ParseFromString(r.content)
        return decoded
    except DecodeError as e:
        app.logger.error(f"DecodeError: {e}")
        return None
    except Exception as e:
        app.logger.error(f"fetch_player error: {e}")
        return None


def _err(msg, code=400):
    return app.response_class(
        response=json.dumps(OrderedDict([("error", msg), ("by", "Rehan Codex")]), separators=(",", ":")),
        status=code,
        mimetype="application/json",
    )


@app.route("/like", methods=["GET"])
def handle_like():
    global used_count

    api_key = request.args.get("key")
    if api_key not in VALID_API_KEYS:
        return _err("Invalid or missing API key", 401)

    uid = request.args.get("uid")
    region = (request.args.get("region") or "").upper()
    if not uid or not region:
        return _err("UID and region are required", 400)

    try:
        tokens = load_tokens(region)
        if not tokens:
            return _err("Failed to load tokens for region", 500)

        token = tokens[0]["token"]
        encrypted_uid = enc(uid)
        if encrypted_uid is None:
            return _err("Encryption of UID failed", 500)

        before = fetch_player(encrypted_uid, region, token)
        if before is None:
            return _err("Failed to get initial info", 500)
        before_like = before.AccountInfo.Likes

        url = region_host(region) + "/LikeProfile"
        asyncio.run(send_multiple_requests(uid, region, url))

        after = fetch_player(encrypted_uid, region, token)
        if after is None:
            return _err("Failed to get final info", 500)
        after_like = after.AccountInfo.Likes

        like_given = after_like - before_like
        status = 1 if like_given > 0 else 2

        if status == 1:
            used_count += 1

        remaining = max(daily_limit - used_count, 0)

        result = OrderedDict([
            ("LikesGivenByAPI", like_given),
            ("LikesafterCommand", after_like),
            ("LikesbeforeCommand", before_like),
            ("PlayerNickname", after.AccountInfo.PlayerNickname),
            ("Level", after.AccountInfo.Levels),
            ("Region", after.AccountInfo.PlayerRegion),
            ("UID", after.AccountInfo.UID),
            ("status", status),
            ("daily_limit", daily_limit),
            ("used", used_count),
            ("remaining", remaining),
            ("by", "Rehan Codex"),
        ])
        return app.response_class(
            response=json.dumps(result, separators=(",", ":")),
            status=200,
            mimetype="application/json",
        )

    except Exception as e:
        app.logger.error(f"handle_like fatal: {e}")
        return _err(str(e), 500)


@app.route("/remain", methods=["GET"])
def remain():
    global used_count
    remaining = max(daily_limit - used_count, 0)
    return jsonify({
        "daily_limit": daily_limit,
        "remaining": remaining,
        "used": used_count,
        "reset_info": "4:00 AM IST",
        "by": "Rehan Codex",
    })


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "Rehan Codex Like API",
        "status": "alive",
        "routes": ["/like?uid=&region=&key=", "/remain"],
    })


# Vercel serverless entry — no app.run()
