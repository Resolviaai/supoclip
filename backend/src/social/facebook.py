"""
Facebook Reels & Video publishing provider for SupoClip using Meta Graph API.

OAuth: Facebook Login OAuth 2.0 with pages_show_list, pages_read_engagement,
       pages_manage_posts, and read_insights.
Upload: Meta Graph API Resumable Video Reels API:
       1. POST /{page_id}/video_reels?upload_phase=start -> upload_url, video_id
       2. POST {upload_url} with binary video bytes
       3. POST /{page_id}/video_reels?upload_phase=finish&video_state=PUBLISHED
Publish: Facebook Reel on Page.
Metrics: Views, likes, comments, and shares via Graph API.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = "https://graph.facebook.com"
GRAPH_API = f"{GRAPH_BASE}/{GRAPH_API_VERSION}"
AUTHORIZE_URL = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"
TOKEN_URL = f"{GRAPH_API}/oauth/access_token"

SCOPES = (
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "read_insights",
)

CAPTION_MAX_LENGTH = 2200
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 300


def _parse_json(resp: requests.Response) -> Dict[str, Any]:
    try:
        val = resp.json()
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _extract_meta_error(resp: requests.Response) -> str:
    data = _parse_json(resp)
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    msg = err.get("message") or resp.text or f"HTTP {resp.status_code}"
    return str(msg)[:240]


class FacebookProvider(SocialProvider):
    name = "facebook"
    display_name = "Facebook (Reels & Video)"
    supported_privacy_levels = ("public",)
    needs_local_file = True

    def __init__(self, config: Optional[Config] = None, session: Optional[requests.Session] = None):
        super().__init__(session)
        self.config = config or get_config()

    @property
    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    @property
    def _app_id(self) -> Optional[str]:
        return (
            getattr(self.config, "facebook_app_id", None)
            or getattr(self.config, "instagram_app_id", None)
            or os.getenv("FACEBOOK_APP_ID")
            or os.getenv("META_APP_ID")
        )

    @property
    def _app_secret(self) -> Optional[str]:
        return (
            getattr(self.config, "facebook_app_secret", None)
            or getattr(self.config, "instagram_app_secret", None)
            or os.getenv("FACEBOOK_APP_SECRET")
            or os.getenv("META_APP_SECRET")
        )

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(
        self, *, state: str, redirect_uri: str, code_challenge: Optional[str] = None
    ) -> str:
        if not self.is_configured:
            raise SocialProviderError("Facebook App ID / Secret not configured")
        params = {
            "client_id": self._app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        params = {
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }
        resp = self.http.get(TOKEN_URL, params=params, timeout=DEFAULT_HTTP_TIMEOUT)
        if resp.status_code != 200:
            raise SocialProviderError(
                f"Failed to exchange Facebook code: {_extract_meta_error(resp)}",
                reauth=True,
            )

        data = _parse_json(resp)
        short_token = data.get("access_token")
        if not short_token:
            raise SocialProviderError("Facebook OAuth response missing access_token", reauth=True)

        # Exchange short-lived token for long-lived (60 days) user token
        long_lived_token, expires_at = self._exchange_for_long_lived_token(short_token)

        # Fetch managed Pages to find the page access token
        page_token, page_id = self._resolve_page_credentials(long_lived_token)
        effective_token = page_token or long_lived_token

        return OAuthTokens(
            access_token=effective_token,
            refresh_token=None,
            expires_at=expires_at,
            scope=",".join(SCOPES),
        )

    def _exchange_for_long_lived_token(self, short_token: str) -> Tuple[str, Optional[datetime]]:
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "fb_exchange_token": short_token,
        }
        try:
            resp = self.http.get(TOKEN_URL, params=params, timeout=DEFAULT_HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = _parse_json(resp)
                tok = data.get("access_token") or short_token
                expires_in = data.get("expires_in")
                exp_dt = (
                    datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                    if expires_in
                    else None
                )
                return tok, exp_dt
        except Exception as e:
            logger.warning(f"Could not exchange for long-lived Facebook token: {e}")
        return short_token, None

    def _resolve_page_credentials(self, user_token: str) -> Tuple[Optional[str], Optional[str]]:
        try:
            url = f"{GRAPH_API}/me/accounts"
            resp = self.http.get(
                url,
                headers={"Authorization": f"Bearer {user_token}"},
                params={"fields": "id,name,access_token"},
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                pages = _parse_json(resp).get("data") or []
                if pages:
                    first = pages[0]
                    return first.get("access_token"), first.get("id")
        except Exception as e:
            logger.warning(f"Error resolving Facebook page credentials: {e}")
        return None, None

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        # Long-lived Facebook page tokens never expire unless password is changed
        return OAuthTokens(access_token=refresh_token, refresh_token=None, expires_at=None)

    def fetch_profile(self, access_token: str) -> AccountProfile:
        url = f"{GRAPH_API}/me"
        resp = self.http.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "id,name,picture{url}"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            raise SocialProviderError(
                f"Failed to fetch Facebook profile: {_extract_meta_error(resp)}",
                reauth=resp.status_code in (401, 403),
            )

        data = _parse_json(resp)
        user_id = str(data.get("id") or "")
        name = data.get("name") or "Facebook Page"
        pic = (data.get("picture") or {}).get("data", {}).get("url")

        return AccountProfile(
            account_id=user_id,
            display_name=name,
            username=name,
            avatar_url=pic,
            raw=data,
        )

    # -- Publishing -----------------------------------------------------
    def publish(self, access_token: str, request: PublishRequest) -> PublishResult:
        if not request.local_file_path or not Path(request.local_file_path).exists():
            raise SocialProviderError("Local video file required for Facebook Reel upload")

        video_path = Path(request.local_file_path)
        file_size = video_path.stat().st_size
        if file_size <= 0:
            raise SocialProviderError("Video file is empty")

        caption = build_caption(request.caption, request.hashtags, CAPTION_MAX_LENGTH)

        # Profile check to get target page/user ID
        profile = self.fetch_profile(access_token)
        target_id = profile.account_id

        logger.info("Initializing Facebook Reel upload for ID %s (%d bytes)", target_id, file_size)

        # Step 1: Start Resumable Reel Session
        init_url = f"{GRAPH_API}/{target_id}/video_reels"
        res = self.http.post(
            init_url,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"upload_phase": "start"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if res.status_code != 200:
            raise SocialProviderError(f"Failed to initiate Facebook Reel: {_extract_meta_error(res)}")

        init_data = _parse_json(res)
        video_id = init_data.get("video_id")
        upload_url = init_data.get("upload_url")

        if not video_id or not upload_url:
            raise SocialProviderError(f"Facebook Reels initialization failed: {init_data}")

        # Step 2: Stream Binary Video Bytes
        logger.info("Uploading video bytes to Facebook upload URL: %s", redact(upload_url))
        with open(video_path, "rb") as f:
            headers = {
                "Authorization": f"OAuth {access_token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream",
                "Content-Length": str(file_size),
            }
            up_res = self.http.post(upload_url, headers=headers, data=f, timeout=180)

        if up_res.status_code not in (200, 201):
            raise SocialProviderError(f"Facebook video upload failed: {_extract_meta_error(up_res)}")

        # Step 3: Finish and Publish Reel
        logger.info("Finalizing Facebook Reel %s...", video_id)
        finish_url = f"{GRAPH_API}/{target_id}/video_reels"
        finish_data = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption,
        }
        fin_res = self.http.post(
            finish_url,
            headers={"Authorization": f"Bearer {access_token}"},
            data=finish_data,
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if fin_res.status_code != 200:
            raise SocialProviderError(f"Facebook Reel finish failed: {_extract_meta_error(fin_res)}")

        # Step 4: Poll status
        remote_url = f"https://www.facebook.com/watch/?v={video_id}"
        deadline = time.time() + POLL_TIMEOUT_SECONDS

        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            st_res = self.http.get(
                f"{GRAPH_API}/{video_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"fields": "status,post_id"},
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if st_res.status_code == 200:
                st_data = _parse_json(st_res)
                v_status = ((st_data.get("status") or {}).get("video_status") or "").lower()
                if v_status in ("ready", "complete", "published"):
                    post_id = st_data.get("post_id") or video_id
                    return PublishResult(
                        external_post_id=str(post_id),
                        external_url=f"https://www.facebook.com/{post_id}" if "_" in str(post_id) else remote_url,
                        raw=st_data,
                    )
                if v_status in ("error", "failed"):
                    raise SocialProviderError(f"Facebook video processing failed: {st_data}", rejected=True)

        # Still processing -> return pending handle
        return PublishResult(
            external_post_id=str(video_id),
            external_url=remote_url,
            pending_handle=json.dumps({"video_id": video_id, "target_id": target_id}),
            raw={"video_id": video_id},
        )

    def resolve_pending(self, access_token: str, pending_handle: str) -> Optional[PublishResult]:
        try:
            data = json.loads(pending_handle)
        except Exception:
            return None

        video_id = data.get("video_id")
        if not video_id:
            return None

        st_res = self.http.get(
            f"{GRAPH_API}/{video_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "status,post_id"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if st_res.status_code == 200:
            st_data = _parse_json(st_res)
            v_status = ((st_data.get("status") or {}).get("video_status") or "").lower()
            if v_status in ("ready", "complete", "published"):
                post_id = st_data.get("post_id") or video_id
                return PublishResult(
                    external_post_id=str(post_id),
                    external_url=f"https://www.facebook.com/watch/?v={video_id}",
                    raw=st_data,
                )
            if v_status in ("error", "failed"):
                raise SocialProviderError(f"Facebook processing failed: {st_data}", rejected=True)
        return None

    def fetch_metrics(
        self, access_token: str, external_post_id: str, account_metadata: Dict[str, Any]
    ) -> PostMetrics:
        """Fetch engagement metrics (views, likes, comments, shares) for a Facebook Reel/Video."""
        url = f"{GRAPH_API}/{external_post_id}"
        params = {"fields": "views,likes.summary(true),comments.summary(true),shares"}
        try:
            resp = self.http.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = _parse_json(resp)
                views = data.get("views")
                likes = (data.get("likes") or {}).get("summary", {}).get("total_count")
                comments = (data.get("comments") or {}).get("summary", {}).get("total_count")
                shares = (data.get("shares") or {}).get("count")
                return PostMetrics(
                    views=views,
                    likes=likes,
                    comments=comments,
                    shares=shares,
                    saves=None,
                    raw=data,
                )
        except Exception as e:
            logger.warning(f"Failed to fetch Facebook metrics for {external_post_id}: {e}")
        return PostMetrics(raw={})
