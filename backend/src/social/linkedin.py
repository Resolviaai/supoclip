"""
LinkedIn video publishing provider for SupoClip using LinkedIn Videos and Posts REST APIs.

OAuth: LinkedIn OAuth 2.0 with openid, profile, email, and w_member_social scopes.
Upload: LinkedIn Videos API initializeUpload -> chunk upload -> finalizeUpload.
Publish: LinkedIn Posts API with video attachment.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
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

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_API_VERSION = os.getenv("LINKEDIN_API_VERSION", "202610")

SCOPES = (
    "openid",
    "profile",
    "email",
    "w_member_social",
)


def _parse_json_safe(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json() if response.content else {}
    except Exception:
        return {}


def _extract_etag(response: requests.Response) -> str:
    headers = getattr(response, "headers", None) or {}
    for key in ("etag", "ETag", "Etag"):
        val = headers.get(key)
        if val:
            return str(val).replace('"', "").strip()
    return ""


def _extract_post_urn(response: requests.Response, fallback_urn: str) -> str:
    headers = getattr(response, "headers", None) or {}
    for key in ("x-restli-id", "X-RestLi-Id", "X-Restli-Id", "x-linkedin-id"):
        val = headers.get(key)
        if val:
            return str(val).strip()
    data = _parse_json_safe(response)
    if data.get("id"):
        return str(data["id"]).strip()
    return fallback_urn


class LinkedInProvider(SocialProvider):
    name = "linkedin"
    display_name = "LinkedIn"
    supported_privacy_levels = ("public",)
    default_privacy_level = "public"
    needs_local_file = True

    def __init__(self, config: Optional[Config] = None, session: Optional[requests.Session] = None):
        super().__init__(session)
        self.config = config or get_config()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.linkedin_client_id and self.config.linkedin_client_secret
        )

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(
        self, *, state: str, redirect_uri: str, code_challenge: Optional[str] = None
    ) -> str:
        params: Dict[str, str] = {
            "response_type": "code",
            "client_id": self.config.linkedin_client_id or "",
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(SCOPES),
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        data: Dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.linkedin_client_id or "",
            "client_secret": self.config.linkedin_client_secret or "",
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        response = self.http.post(TOKEN_URL, data=data, timeout=DEFAULT_HTTP_TIMEOUT)
        if response.status_code >= 400:
            error_data = _parse_json_safe(response)
            msg = error_data.get("error_description") or error_data.get("message") or response.text
            raise SocialProviderError(
                f"LinkedIn token exchange failed ({response.status_code}): {msg}",
                reauth=response.status_code in (400, 401),
                retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            )

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise SocialProviderError("LinkedIn token exchange did not return an access_token")

        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )
        refresh_token = payload.get("refresh_token")

        return OAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=payload.get("scope"),
            raw=payload,
        )

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.config.linkedin_client_id or "",
            "client_secret": self.config.linkedin_client_secret or "",
        }
        response = self.http.post(TOKEN_URL, data=data, timeout=DEFAULT_HTTP_TIMEOUT)
        if response.status_code >= 400:
            error_data = _parse_json_safe(response)
            msg = error_data.get("error_description") or response.text
            raise SocialProviderError(
                f"LinkedIn token refresh failed ({response.status_code}): {msg}",
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
        response = self.http.get(USERINFO_URL, headers=headers, timeout=DEFAULT_HTTP_TIMEOUT)
        if response.status_code >= 400:
            raise SocialProviderError(
                f"LinkedIn profile fetch failed ({response.status_code}): {response.text}",
                reauth=response.status_code in (401, 403),
                retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            )

        payload = response.json()
        sub = payload.get("sub")
        if not sub:
            raise SocialProviderError("LinkedIn userinfo response missing sub identifier")

        author_urn = f"urn:li:person:{sub}"
        name = payload.get("name") or payload.get("given_name", "LinkedIn User")
        email = payload.get("email")
        picture = payload.get("picture")

        return AccountProfile(
            external_account_id=author_urn,
            display_name=name,
            username=email or name,
            avatar_url=picture,
            metadata={"urn": author_urn, "sub": sub, "email": email},
        )

    # -- Publishing -----------------------------------------------------
    def publish(self, access_token: str, request: PublishRequest) -> PublishResult:
        file_path = Path(request.file_path)
        if not file_path.exists():
            raise SocialProviderError(f"Video file missing: {file_path}")

        file_size = file_path.stat().st_size
        if file_size <= 0:
            raise SocialProviderError(f"Video file is empty: {file_path}")

        author_urn = request.account_metadata.get("urn")
        if not author_urn:
            profile = self.fetch_profile(access_token)
            author_urn = profile.external_account_id

        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

        # Step 1: Initialize upload
        init_url = "https://api.linkedin.com/rest/videos?action=initializeUpload"
        init_body = {
            "initializeUploadRequest": {
                "owner": author_urn,
                "fileSizeBytes": file_size,
                "uploadCaptions": False,
                "uploadThumbnail": False,
            }
        }

        try:
            init_res = self.http.post(init_url, headers=headers, json=init_body, timeout=(15, 30))
            if init_res.status_code in (401, 403):
                raise SocialProviderError(
                    f"LinkedIn authentication failed during initializeUpload: {init_res.text}",
                    reauth=True,
                )
            if init_res.status_code >= 400:
                raise SocialProviderError(
                    f"LinkedIn initializeUpload failed ({init_res.status_code}): {init_res.text}",
                    retryable=init_res.status_code in (408, 429, 500, 502, 503, 504),
                )
        except requests.RequestException as e:
            raise SocialProviderError(f"Network error during LinkedIn initializeUpload: {e}", retryable=True) from e

        init_data = _parse_json_safe(init_res).get("value") or {}
        video_urn = init_data.get("video")
        upload_token = str(init_data.get("uploadToken") or "")
        instructions = init_data.get("uploadInstructions") or []

        if not video_urn or not instructions:
            raise SocialProviderError(f"LinkedIn video upload initialization failed: {init_data}")

        # Step 2: Upload chunks
        uploaded_part_ids: List[str] = []
        try:
            with open(file_path, "rb") as f:
                for inst in instructions:
                    part_url = inst.get("uploadUrl")
                    if not part_url:
                        continue
                    first_byte = int(inst.get("firstByte", 0) or 0)
                    last_byte = int(inst.get("lastByte", file_size - 1) if inst.get("lastByte") is not None else file_size - 1)
                    part_len = max(0, min(file_size, last_byte + 1) - max(0, first_byte))
                    if part_len <= 0:
                        continue
                    f.seek(first_byte)
                    chunk_data = f.read(part_len)

                    up_headers = {
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(part_len),
                    }
                    up_res = self.http.put(part_url, headers=up_headers, data=chunk_data, timeout=(15, 180))
                    if up_res.status_code >= 400:
                        raise SocialProviderError(
                            f"LinkedIn chunk upload failed ({up_res.status_code}): {up_res.text}",
                            retryable=up_res.status_code in (408, 429, 500, 502, 503, 504),
                        )
                    etag = _extract_etag(up_res)
                    if etag:
                        uploaded_part_ids.append(etag)
        except requests.RequestException as e:
            raise SocialProviderError(f"Network error during LinkedIn chunk upload: {e}", retryable=True) from e

        # Step 3: Finalize upload
        fin_url = "https://api.linkedin.com/rest/videos?action=finalizeUpload"
        fin_body = {
            "finalizeUploadRequest": {
                "video": video_urn,
                "uploadToken": upload_token,
                "uploadedPartIds": uploaded_part_ids,
            }
        }
        try:
            fin_res = self.http.post(fin_url, headers=headers, json=fin_body, timeout=(15, 30))
            if fin_res.status_code >= 400:
                raise SocialProviderError(
                    f"LinkedIn finalizeUpload failed ({fin_res.status_code}): {fin_res.text}",
                    reauth=fin_res.status_code in (401, 403),
                    retryable=fin_res.status_code in (408, 429, 500, 502, 503, 504),
                )
        except requests.RequestException as e:
            raise SocialProviderError(f"Network error during LinkedIn finalizeUpload: {e}", retryable=True) from e

        # Step 4: Wait for processing or return pending handle
        caption = build_caption(request.caption, request.hashtags)
        encoded_urn = urllib.parse.quote(str(video_urn), safe="")
        status_url = f"https://api.linkedin.com/rest/videos/{encoded_urn}"
        is_ready = False

        for idx in range(6):  # Poll up to ~18s
            time.sleep(3.0)
            st_res = self.http.get(status_url, headers=headers, timeout=(10, 15))
            if st_res.status_code == 200:
                st_val = str(_parse_json_safe(st_res).get("status") or "").upper()
                if st_val == "AVAILABLE":
                    is_ready = True
                    break
                if st_val == "PROCESSING_FAILED":
                    raise SocialProviderError(f"LinkedIn video processing failed for {video_urn}", rejected=True)

        if not is_ready:
            # Video is still processing on LinkedIn. Return pending handle for worker resolution.
            pending_handle = json.dumps({
                "video_urn": video_urn,
                "author_urn": author_urn,
                "title": request.title,
                "caption": caption,
            })
            logger.info("LinkedIn video %s still processing; deferred to pending handle", video_urn)
            return PublishResult(
                external_post_id=None,
                external_url=None,
                pending_handle=pending_handle,
                raw={"video_urn": video_urn},
            )

        # Step 5: Create Post
        return self._create_post(headers, author_urn, video_urn, request.title, caption)

    def _create_post(
        self,
        headers: Dict[str, str],
        author_urn: str,
        video_urn: str,
        title: str,
        caption: str,
    ) -> PublishResult:
        post_url = "https://api.linkedin.com/rest/posts"
        post_body = {
            "author": author_urn,
            "commentary": (caption or title)[:3000],
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": {
                "media": {
                    "title": (title or caption[:50])[:200],
                    "id": video_urn,
                }
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        with ambiguous_outcome("LinkedIn create post"):
            res = self.http.post(post_url, headers=headers, json=post_body, timeout=(15, 30))

        if res.status_code >= 400:
            raise SocialProviderError(
                f"LinkedIn create post failed ({res.status_code}): {res.text}",
                reauth=res.status_code in (401, 403),
                retryable=res.status_code in (408, 429, 500, 502, 503, 504),
            )

        post_urn = _extract_post_urn(res, str(video_urn))
        remote_url = f"https://www.linkedin.com/feed/update/{post_urn}"

        logger.info("Published video to LinkedIn: %s", remote_url)
        return PublishResult(
            external_post_id=post_urn,
            external_url=remote_url,
            raw=res.json() if res.content else {},
        )

    def resolve_pending(
        self, access_token: str, pending_handle: str
    ) -> Optional[PublishResult]:
        try:
            handle_data = json.loads(pending_handle)
        except Exception:
            return None

        video_urn = handle_data.get("video_urn")
        author_urn = handle_data.get("author_urn")
        title = handle_data.get("title", "")
        caption = handle_data.get("caption", "")

        if not video_urn or not author_urn:
            return None

        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

        encoded_urn = urllib.parse.quote(str(video_urn), safe="")
        status_url = f"https://api.linkedin.com/rest/videos/{encoded_urn}"
        st_res = self.http.get(status_url, headers=headers, timeout=DEFAULT_HTTP_TIMEOUT)

        if st_res.status_code == 200:
            status_val = str(_parse_json_safe(st_res).get("status") or "").upper()
            if status_val == "AVAILABLE":
                return self._create_post(headers, author_urn, video_urn, title, caption)
            if status_val == "PROCESSING_FAILED":
                raise SocialProviderError(f"LinkedIn video processing failed for {video_urn}", rejected=True)

        return None

    def fetch_metrics(
        self, access_token: str, external_post_id: str, account_metadata: Dict[str, Any]
    ) -> PostMetrics:
        """Fetch post social metrics (likes/comments) from LinkedIn."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
        }
        urn = external_post_id if external_post_id.startswith("urn:li:") else f"urn:li:share:{external_post_id}"
        encoded_urn = urllib.parse.quote(urn, safe="")
        url = f"https://api.linkedin.com/rest/socialActions/{encoded_urn}"
        try:
            resp = self.http.get(url, headers=headers, timeout=DEFAULT_HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                likes = (data.get("likesSummary") or {}).get("totalLikes")
                comments = (data.get("commentsSummary") or {}).get("totalComments")
                return PostMetrics(
                    views=None,
                    likes=likes,
                    comments=comments,
                    shares=None,
                    saves=None,
                    raw=data,
                )
        except Exception as e:
            logger.warning(f"Failed to fetch LinkedIn metrics for {external_post_id}: {e}")
        return PostMetrics(raw={})
