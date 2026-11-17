"""
X (formerly Twitter) video publishing provider for SupoClip using X API v2 and Media Upload.

OAuth: X OAuth 2.0 PKCE with tweet.read, tweet.write, users.read, and offline.access scopes.
Upload: X Media Upload (INIT -> APPEND -> FINALIZE -> STATUS).
Publish: X API v2 /2/tweets with media attachment.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from ..config import Config, get_config
from .base import (
    DEFAULT_HTTP_TIMEOUT,
    AccountProfile,
    OAuthTokens,
    PostMetrics,
    PublishRequest,
    PublishResult,
    SocialProvider,
    SocialProviderError,
    ambiguous_outcome,
    build_caption,
    redact,
)

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
USERS_ME_URL = "https://api.twitter.com/2/users/me"
TWEETS_URL = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"

SCOPES = (
    "tweet.read",
    "tweet.write",
    "users.read",
    "offline.access",
)

CHUNK_SIZE = 5 * 1024 * 1024  # 5 MiB


def _parse_json_safe(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json() if response.content else {}
    except Exception:
        return {}


class TwitterProvider(SocialProvider):
    name = "twitter"
    display_name = "X (Twitter)"
    supported_privacy_levels = ("public",)
    default_privacy_level = "public"
    needs_local_file = True

    def __init__(self, config: Optional[Config] = None, session: Optional[requests.Session] = None):
        super().__init__(session)
        self.config = config or get_config()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.twitter_client_id and self.config.twitter_client_secret
        ) or bool(self.config.twitter_bearer_token)

    @property
    def uses_pkce(self) -> bool:
        return True

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(
        self, *, state: str, redirect_uri: str, code_challenge: Optional[str] = None
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self.config.twitter_client_id or "",
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": code_challenge or "challenge",
            "code_challenge_method": "S256",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        auth_header = None
        if self.config.twitter_client_id and self.config.twitter_client_secret:
            cred = f"{self.config.twitter_client_id}:{self.config.twitter_client_secret}"
            auth_header = "Basic " + base64.b64encode(cred.encode()).decode()

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if auth_header:
            headers["Authorization"] = auth_header

        data = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier or "",
            "client_id": self.config.twitter_client_id or "",
        }

        response = self.http.post(TOKEN_URL, headers=headers, data=data, timeout=DEFAULT_HTTP_TIMEOUT)
        if response.status_code >= 400:
            error_data = _parse_json_safe(response)
            msg = error_data.get("error_description") or error_data.get("message") or response.text
            raise SocialProviderError(
                f"X / Twitter token exchange failed ({response.status_code}): {msg}",
                reauth=response.status_code in (400, 401),
                retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            )

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise SocialProviderError("X / Twitter token exchange did not return an access_token")

        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )

        return OAuthTokens(
            access_token=access_token,
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
            scopes=payload.get("scope"),
            raw=payload,
        )

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        auth_header = None
        if self.config.twitter_client_id and self.config.twitter_client_secret:
            cred = f"{self.config.twitter_client_id}:{self.config.twitter_client_secret}"
            auth_header = "Basic " + base64.b64encode(cred.encode()).decode()

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if auth_header:
            headers["Authorization"] = auth_header

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.config.twitter_client_id or "",
        }

        response = self.http.post(TOKEN_URL, headers=headers, data=data, timeout=DEFAULT_HTTP_TIMEOUT)
        if response.status_code >= 400:
            error_data = _parse_json_safe(response)
            msg = error_data.get("error_description") or response.text
            raise SocialProviderError(
                f"X / Twitter token refresh failed ({response.status_code}): {msg}",
                reauth=response.status_code in (400, 401),
                retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            )

        payload = response.json()
        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )

        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token") or refresh_token,
            expires_at=expires_at,
            scopes=payload.get("scope"),
            raw=payload,
        )

    def fetch_profile(self, access_token: str) -> AccountProfile:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{USERS_ME_URL}?user.fields=profile_image_url,username,name"
        response = self.http.get(url, headers=headers, timeout=DEFAULT_HTTP_TIMEOUT)
        if response.status_code >= 400:
            raise SocialProviderError(
                f"X / Twitter profile fetch failed ({response.status_code}): {response.text}",
                reauth=response.status_code in (401, 403),
                retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            )

        payload = response.json().get("data") or {}
        user_id = str(payload.get("id") or "")
        username = payload.get("username", "")
        name = payload.get("name") or username

        return AccountProfile(
            external_account_id=user_id,
            display_name=name,
            username=f"@{username}" if username else None,
            avatar_url=payload.get("profile_image_url"),
            metadata={"id": user_id, "username": username},
        )

    # -- Publishing -----------------------------------------------------
    def publish(self, access_token: str, request: PublishRequest) -> PublishResult:
        file_path = Path(request.file_path)
        if not file_path.exists():
            raise SocialProviderError(f"Video file missing: {file_path}")

        file_size = file_path.stat().st_size
        if file_size <= 0:
            raise SocialProviderError(f"Video file is empty: {file_path}")

        headers = {"Authorization": f"Bearer {access_token}"}

        # Step 1: INIT
        init_data = {
            "command": "INIT",
            "total_bytes": str(file_size),
            "media_type": "video/mp4",
            "media_category": "tweet_video",
        }
        try:
            init_res = self.http.post(MEDIA_UPLOAD_URL, headers=headers, data=init_data, timeout=(15, 30))
            if init_res.status_code in (401, 403):
                raise SocialProviderError(f"X / Twitter authorization expired: {init_res.text}", reauth=True)
            if init_res.status_code >= 400:
                raise SocialProviderError(
                    f"X media INIT failed ({init_res.status_code}): {init_res.text}",
                    retryable=init_res.status_code in (408, 429, 500, 502, 503, 504),
                )
        except requests.RequestException as e:
            raise SocialProviderError(f"Network error during X media INIT: {e}", retryable=True) from e

        media_id = str(_parse_json_safe(init_res).get("media_id_string") or _parse_json_safe(init_res).get("media_id") or "")
        if not media_id:
            raise SocialProviderError("X media INIT returned no media_id")

        # Step 2: APPEND chunks
        segment_index = 0
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    append_res = self.http.post(
                        MEDIA_UPLOAD_URL,
                        headers=headers,
                        data={
                            "command": "APPEND",
                            "media_id": media_id,
                            "segment_index": str(segment_index),
                        },
                        files={"media": chunk},
                        timeout=(15, 120),
                    )
                    if append_res.status_code >= 400:
                        raise SocialProviderError(
                            f"X media APPEND segment {segment_index} failed ({append_res.status_code}): {append_res.text}",
                            retryable=append_res.status_code in (408, 429, 500, 502, 503, 504),
                        )
                    segment_index += 1
        except requests.RequestException as e:
            raise SocialProviderError(f"Network error during X media APPEND: {e}", retryable=True) from e

        # Step 3: FINALIZE
        try:
            fin_res = self.http.post(
                MEDIA_UPLOAD_URL,
                headers=headers,
                data={"command": "FINALIZE", "media_id": media_id},
                timeout=(15, 30),
            )
            if fin_res.status_code >= 400:
                raise SocialProviderError(
                    f"X media FINALIZE failed ({fin_res.status_code}): {fin_res.text}",
                    retryable=fin_res.status_code in (408, 429, 500, 502, 503, 504),
                )
        except requests.RequestException as e:
            raise SocialProviderError(f"Network error during X media FINALIZE: {e}", retryable=True) from e

        fin_data = _parse_json_safe(fin_res)
        processing_info = fin_data.get("processing_info")

        # Step 4: STATUS check if processing
        text = build_caption(request.caption, request.hashtags)
        if not text and request.title:
            text = request.title

        is_ready = True
        if processing_info:
            is_ready = False
            for _ in range(8):  # Poll up to ~24s
                check_after = float(processing_info.get("check_after_secs", 2.0))
                time.sleep(max(1.0, check_after))

                st_res = self.http.get(
                    MEDIA_UPLOAD_URL,
                    headers=headers,
                    params={"command": "STATUS", "media_id": media_id},
                    timeout=(10, 15),
                )
                if st_res.status_code == 200:
                    info = _parse_json_safe(st_res).get("processing_info") or {}
                    state = str(info.get("state") or "").lower()
                    if state == "succeeded":
                        is_ready = True
                        break
                    if state == "failed":
                        err_msg = info.get("error", {}).get("message", "Processing failed")
                        raise SocialProviderError(f"X video processing failed: {err_msg}", rejected=True)
                    processing_info = info

        if not is_ready:
            pending_handle = json.dumps({
                "media_id": media_id,
                "text": text,
            })
            logger.info("X video %s still processing; deferred to pending handle", media_id)
            return PublishResult(
                external_post_id=None,
                external_url=None,
                pending_handle=pending_handle,
                raw={"media_id": media_id},
            )

        # Step 5: Create Tweet
        return self._create_tweet(headers, media_id, text)

    def _create_tweet(self, headers: Dict[str, str], media_id: str, text: str) -> PublishResult:
        tweet_body = {
            "text": text[:280],
            "media": {
                "media_ids": [media_id]
            }
        }
        headers = {**headers, "Content-Type": "application/json"}

        with ambiguous_outcome("X create tweet"):
            res = self.http.post(TWEETS_URL, headers=headers, json=tweet_body, timeout=(15, 30))

        if res.status_code >= 400:
            raise SocialProviderError(
                f"X create tweet failed ({res.status_code}): {res.text}",
                reauth=res.status_code in (401, 403),
                retryable=res.status_code in (408, 429, 500, 502, 503, 504),
            )

        data = _parse_json_safe(res).get("data") or {}
        tweet_id = str(data.get("id") or "")
        remote_url = f"https://x.com/i/status/{tweet_id}" if tweet_id else None

        logger.info("Published tweet to X: %s", remote_url)
        return PublishResult(
            external_post_id=tweet_id,
            external_url=remote_url,
            raw=data,
        )

    def resolve_pending(
        self, access_token: str, pending_handle: str
    ) -> Optional[PublishResult]:
        try:
            handle_data = json.loads(pending_handle)
        except Exception:
            return None

        media_id = handle_data.get("media_id")
        text = handle_data.get("text", "")
        if not media_id:
            return None

        headers = {"Authorization": f"Bearer {access_token}"}
        st_res = self.http.get(
            MEDIA_UPLOAD_URL,
            headers=headers,
            params={"command": "STATUS", "media_id": media_id},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if st_res.status_code == 200:
            info = _parse_json_safe(st_res).get("processing_info") or {}
            state = str(info.get("state") or "").lower()
            if state == "succeeded":
                return self._create_tweet(headers, media_id, text)
            if state == "failed":
                err_msg = info.get("error", {}).get("message", "Processing failed")
                raise SocialProviderError(f"X video processing failed: {err_msg}", rejected=True)

        return None

    def fetch_metrics(
        self, access_token: str, external_post_id: str, account_metadata: Dict[str, Any]
    ) -> PostMetrics:
        """Fetch tweet public metrics (retweets, replies, likes, quotes, impressions) from X/Twitter API v2."""
        headers = self._auth_headers(access_token)
        url = f"{TWITTER_API_V2}/tweets/{external_post_id}"
        params = {"tweet.fields": "public_metrics,non_public_metrics"}
        try:
            resp = self.http.get(url, headers=headers, params=params, timeout=DEFAULT_HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                metrics = (data.get("data") or {}).get("public_metrics") or {}
                return PostMetrics(
                    views=metrics.get("impression_count"),
                    likes=metrics.get("like_count"),
                    comments=metrics.get("reply_count"),
                    shares=(metrics.get("retweet_count", 0) or 0) + (metrics.get("quote_count", 0) or 0),
                    saves=metrics.get("bookmark_count"),
                    raw=metrics,
                )
        except Exception as e:
            logger.warning(f"Failed to fetch Twitter metrics for {external_post_id}: {e}")
        return PostMetrics(raw={})
