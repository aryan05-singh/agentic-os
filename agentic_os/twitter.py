"""X (Twitter) posting — OAuth 1.0a request signing via stdlib, no SDK.

Unlike Gmail, this needs no interactive OAuth dance: the four credentials
(api key/secret, access token/secret) are generated once in the X Developer
Portal for your own account and never expire on their own, so posting is a
single signed HTTP POST.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

TWEET_URL = "https://api.twitter.com/2/tweets"


def _percent_encode(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _oauth_header(config: dict, method: str, url: str) -> str:
    params = {
        "oauth_consumer_key": config["twitter_api_key"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": config["twitter_access_token"],
        "oauth_version": "1.0",
    }

    param_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted(params.items())
    )
    base_string = "&".join([method.upper(), _percent_encode(url), _percent_encode(param_string)])
    signing_key = f"{_percent_encode(config['twitter_api_secret'])}&{_percent_encode(config['twitter_access_token_secret'])}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = signature

    header_params = ", ".join(f'{_percent_encode(k)}="{_percent_encode(v)}"' for k, v in sorted(params.items()))
    return f"OAuth {header_params}"


def configured(config: dict) -> bool:
    return bool(
        config.get("twitter_api_key")
        and config.get("twitter_api_secret")
        and config.get("twitter_access_token")
        and config.get("twitter_access_token_secret")
    )


def post_tweet(config: dict, text: str) -> dict:
    """Posts text as a tweet. Returns {"id", "url"}. Raises RuntimeError on failure."""
    auth_header = _oauth_header(config, "POST", TWEET_URL)
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        TWEET_URL,
        data=body,
        method="POST",
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"X API {e.code}: {detail}") from e

    tweet_id = data["data"]["id"]
    return {"id": tweet_id, "url": f"https://twitter.com/i/web/status/{tweet_id}"}
