"""Cloud Social Publishing Runner.
Runs unattended in GitHub Actions or locally.
Checks schedule_export.json, fetches media from Cloudflare R2, and dispatches due posts
to YouTube, Facebook, Instagram, LinkedIn, and X.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import boto3
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cloud_publish_runner")

SCHEDULE_FILE = Path(__file__).parent / "schedule_export.json"
VAULT_FILE = Path(__file__).parent / "accounts_vault.json"

def load_accounts() -> Dict[str, Any]:
    # 1. Environment variable (for GitHub Actions Secret: SOCIAL_ACCOUNTS_VAULT)
    env_vault = os.getenv("SOCIAL_ACCOUNTS_VAULT", "").strip()
    if env_vault:
        try:
            import base64
            # Check if base64 encoded
            try:
                decoded = base64.b64decode(env_vault).decode("utf-8")
                return json.loads(decoded)
            except Exception:
                return json.loads(env_vault)
        except Exception as e:
            logger.error("Failed to parse SOCIAL_ACCOUNTS_VAULT from environment: %s", e)

    # 2. Local secure vault file
    if VAULT_FILE.exists():
        try:
            with open(VAULT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load %s: %s", VAULT_FILE, e)

    return {}

# S3 / Cloudflare R2 Configuration
MEDIA_S3_ENDPOINT = os.getenv("MEDIA_S3_ENDPOINT", "https://75d8978aa7345a0df28e89bff1e847ec.r2.cloudflarestorage.com")
MEDIA_S3_BUCKET = os.getenv("MEDIA_S3_BUCKET", "dispatch-clips")
MEDIA_S3_ACCESS_KEY_ID = os.getenv("MEDIA_S3_ACCESS_KEY_ID", "")
MEDIA_S3_SECRET_ACCESS_KEY = os.getenv("MEDIA_S3_SECRET_ACCESS_KEY", "")

# Social Platform Client Secrets
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_OAUTH_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET", "")
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
TWITTER_CLIENT_ID = os.getenv("TWITTER_CLIENT_ID", "")
TWITTER_CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET", "")


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MEDIA_S3_ENDPOINT,
        aws_access_key_id=MEDIA_S3_ACCESS_KEY_ID,
        aws_secret_access_key=MEDIA_S3_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def extract_s3_key(s3_uri: str) -> str:
    """Extract key from s3://dispatch-clips/supoclip/clips/... or raw key."""
    if s3_uri.startswith("s3://"):
        parts = s3_uri[5:].split("/", 1)
        return parts[1] if len(parts) > 1 else parts[0]
    return s3_uri.lstrip("/")


def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    s3 = get_s3_client()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": MEDIA_S3_BUCKET, "Key": s3_key},
        ExpiresIn=expires_in,
    )


def download_clip_to_temp(s3_key: str) -> Path:
    s3 = get_s3_client()
    suffix = Path(s3_key).suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    logger.info("Downloading r2://%s/%s -> %s", MEDIA_S3_BUCKET, s3_key, tmp_path)
    s3.download_file(MEDIA_S3_BUCKET, s3_key, str(tmp_path))
    return tmp_path


# --- Token Refresh Functions ---

def refresh_google_token(refresh_token: str) -> str:
    res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if res.status_code != 200:
        raise RuntimeError(f"Google token refresh failed ({res.status_code}): {res.text}")
    return res.json()["access_token"]


def refresh_twitter_token(refresh_token: str) -> tuple[str, str]:
    res = requests.post(
        "https://api.twitter.com/2/oauth2/token",
        data={
            "client_id": TWITTER_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(TWITTER_CLIENT_ID, TWITTER_CLIENT_SECRET),
        timeout=30,
    )
    if res.status_code != 200:
        raise RuntimeError(f"Twitter token refresh failed ({res.status_code}): {res.text}")
    data = res.json()
    return data["access_token"], data.get("refresh_token", refresh_token)


# --- Platform Publishers ---

def publish_youtube(account: Dict[str, Any], post: Dict[str, Any], local_path: Path) -> tuple[str, str]:
    refresh_token = account.get("refresh_token")
    access_token = refresh_google_token(refresh_token) if refresh_token else account["access_token"]

    file_size = local_path.stat().st_size
    title = (post.get("title") or "Untitled Clip")[:100]
    description = post.get("caption") or title
    hashtags = post.get("hashtags") or []
    if not any("shorts" in h.lower() for h in hashtags):
        hashtags.append("Shorts")
    tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    full_description = f"{description}\n\n{tag_str}".strip()[:5000]

    init_res = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_size),
        },
        json={
            "snippet": {
                "title": title,
                "description": full_description,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": post.get("privacy_level", "public"),
                "selfDeclaredMadeForKids": False,
            },
        },
        timeout=30,
    )
    if init_res.status_code not in (200, 201):
        raise RuntimeError(f"YouTube upload init failed ({init_res.status_code}): {init_res.text}")

    upload_url = init_res.headers.get("Location")
    with open(local_path, "rb") as f:
        upload_res = requests.put(
            upload_url,
            headers={"Content-Type": "video/mp4", "Content-Length": str(file_size)},
            data=f,
            timeout=300,
        )

    if upload_res.status_code not in (200, 201):
        raise RuntimeError(f"YouTube upload chunks failed ({upload_res.status_code}): {upload_res.text}")

    video_id = upload_res.json().get("id")
    video_url = f"https://www.youtube.com/shorts/{video_id}"
    return video_id, video_url


def publish_facebook(account: Dict[str, Any], post: Dict[str, Any], local_path: Path) -> tuple[str, str]:
    access_token = account["access_token"]
    file_size = local_path.stat().st_size
    target_id = account.get("external_account_id") or "me"

    # Step 1: Start reel upload session
    init_url = f"https://graph.facebook.com/v21.0/{target_id}/video_reels"
    init_res = requests.post(
        init_url,
        data={"upload_phase": "start", "access_token": access_token},
        timeout=30,
    )
    if init_res.status_code != 200:
        raise RuntimeError(f"FB Reel init failed ({init_res.status_code}): {init_res.text}")
    init_data = init_res.json()
    video_id = init_data.get("video_id")
    upload_url = init_data.get("upload_url")

    # Step 2: Upload binary
    with open(local_path, "rb") as f:
        upload_res = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {access_token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream",
            },
            data=f,
            timeout=300,
        )
    if upload_res.status_code not in (200, 201):
        raise RuntimeError(f"FB Reel binary upload failed: {upload_res.text}")

    # Step 3: Finish and publish
    description = post.get("caption") or post.get("title") or ""
    finish_res = requests.post(
        init_url,
        data={
            "upload_phase": "finish",
            "access_token": access_token,
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
        },
        timeout=30,
    )
    if finish_res.status_code != 200:
        raise RuntimeError(f"FB Reel publish failed: {finish_res.text}")

    post_url = f"https://www.facebook.com/reel/{video_id}"
    return video_id, post_url


def publish_instagram(account: Dict[str, Any], post: Dict[str, Any], presigned_url: str) -> tuple[str, str]:
    access_token = account["access_token"]
    ig_user_id = account.get("external_account_id")
    caption = post.get("caption") or post.get("title") or ""
    hashtags = post.get("hashtags") or []
    if hashtags:
        caption += "\n\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)

    # Step 1: Create media container
    create_res = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": presigned_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token,
        },
        timeout=30,
    )
    if create_res.status_code != 200:
        raise RuntimeError(f"Instagram container creation failed: {create_res.text}")
    container_id = create_res.json()["id"]

    # Poll container status (takes 5-15 seconds for IG to process)
    import time
    for _ in range(15):
        time.sleep(5)
        status_res = requests.get(
            f"https://graph.facebook.com/v21.0/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        if status_res.status_code == 200:
            status_code = status_res.json().get("status_code")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                raise RuntimeError(f"Instagram media processing error: {status_res.text}")

    # Step 2: Publish container
    pub_res = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    if pub_res.status_code != 200:
        raise RuntimeError(f"Instagram media publish failed: {pub_res.text}")
    media_id = pub_res.json().get("id")
    ig_url = f"https://www.instagram.com/reel/{media_id}/"
    return media_id, ig_url


def publish_linkedin(account: Dict[str, Any], post: Dict[str, Any], local_path: Path) -> tuple[str, str]:
    access_token = account["access_token"]
    author_urn = account.get("metadata", {}).get("author_urn") or account.get("external_account_id")
    file_size = local_path.stat().st_size
    title = (post.get("title") or "Professional Insights")[:200]
    commentary = post.get("caption") or title

    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": "202401",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }

    # Step 1: Initialize video upload
    init_payload = {
        "initializeUploadRequest": {
            "owner": author_urn,
            "fileSizeBytes": file_size,
            "uploadCaptions": False,
            "uploadThumbnail": False,
        }
    }
    init_res = requests.post(
        "https://api.linkedin.com/rest/videos?action=initializeUpload",
        headers=headers,
        json=init_payload,
        timeout=30,
    )
    if init_res.status_code not in (200, 201):
        raise RuntimeError(f"LinkedIn init upload failed ({init_res.status_code}): {init_res.text}")

    init_data = init_res.json()["value"]
    video_urn = init_data["video"]
    upload_instructions = init_data["uploadInstructions"][0]
    upload_url = upload_instructions["uploadUrl"]

    # Step 2: Upload binary
    with open(local_path, "rb") as f:
        put_res = requests.put(
            upload_url,
            headers={"Content-Type": "application/octet-stream"},
            data=f,
            timeout=300,
        )
    if put_res.status_code not in (200, 201):
        raise RuntimeError(f"LinkedIn video chunk PUT failed: {put_res.text}")

    # Step 3: Create Post
    post_payload = {
        "author": author_urn,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "title": title,
                "id": video_urn,
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    post_res = requests.post("https://api.linkedin.com/rest/posts", headers=headers, json=post_payload, timeout=30)
    if post_res.status_code not in (200, 201):
        raise RuntimeError(f"LinkedIn create post failed: {post_res.text}")

    post_urn = post_res.headers.get("x-restli-id") or video_urn
    post_url = f"https://www.linkedin.com/feed/update/{post_urn}"
    return post_urn, post_url


def publish_twitter(account: Dict[str, Any], post: Dict[str, Any], local_path: Path) -> tuple[str, str]:
    refresh_token = account.get("refresh_token")
    if refresh_token:
        try:
            access_token, new_refresh = refresh_twitter_token(refresh_token)
            account["access_token"] = access_token
            account["refresh_token"] = new_refresh
        except Exception as e:
            logger.warning("Twitter refresh token failed, trying stored access token: %s", e)
            access_token = account["access_token"]
    else:
        access_token = account["access_token"]

    from requests_oauthlib import OAuth1
    x_api_key = os.getenv("X_API_KEY", "")
    x_api_secret = os.getenv("X_API_SECRET", "")

    # Use OAuth 2.0 User Token Bearer for Twitter v2 or OAuth 1.0a upload
    file_size = local_path.stat().st_size
    text = (post.get("caption") or post.get("title") or "")[:280]

    # Chunked upload via upload.twitter.com/1.1/media/upload.json
    # INIT
    init_res = requests.post(
        "https://upload.twitter.com/1.1/media/upload.json",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "command": "INIT",
            "total_bytes": str(file_size),
            "media_type": "video/mp4",
            "media_category": "tweet_video",
        },
        timeout=30,
    )
    if init_res.status_code not in (200, 201, 202):
        raise RuntimeError(f"Twitter upload INIT failed ({init_res.status_code}): {init_res.text}")
    media_id = str(init_res.json()["media_id_string"])

    # APPEND
    with open(local_path, "rb") as f:
        segment_id = 0
        while True:
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            append_res = requests.post(
                "https://upload.twitter.com/1.1/media/upload.json",
                headers={"Authorization": f"Bearer {access_token}"},
                data={
                    "command": "APPEND",
                    "media_id": media_id,
                    "segment_index": str(segment_id),
                },
                files={"media": chunk},
                timeout=120,
            )
            if append_res.status_code not in (200, 201, 202, 204):
                raise RuntimeError(f"Twitter upload APPEND failed: {append_res.text}")
            segment_id += 1

    # FINALIZE
    fin_res = requests.post(
        "https://upload.twitter.com/1.1/media/upload.json",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"command": "FINALIZE", "media_id": media_id},
        timeout=30,
    )
    if fin_res.status_code not in (200, 201):
        raise RuntimeError(f"Twitter upload FINALIZE failed: {fin_res.text}")

    # Check status if processing
    fin_data = fin_res.json()
    processing_info = fin_data.get("processing_info")
    if processing_info:
        import time
        for _ in range(10):
            wait_sec = processing_info.get("check_after_secs", 5)
            time.sleep(wait_sec)
            status_res = requests.get(
                "https://upload.twitter.com/1.1/media/upload.json",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"command": "STATUS", "media_id": media_id},
                timeout=30,
            )
            if status_res.status_code == 200:
                s_data = status_res.json()
                p_state = s_data.get("processing_info", {}).get("state")
                if p_state == "succeeded":
                    break
                if p_state == "failed":
                    raise RuntimeError(f"Twitter video processing failed: {status_res.text}")

    # TWEET
    tweet_res = requests.post(
        "https://api.twitter.com/2/tweets",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"text": text, "media": {"media_ids": [media_id]}},
        timeout=30,
    )
    if tweet_res.status_code not in (200, 201):
        raise RuntimeError(f"Twitter post tweet failed: {tweet_res.text}")

    tweet_id = tweet_res.json().get("data", {}).get("id")
    tweet_url = f"https://x.com/i/status/{tweet_id}"
    return tweet_id, tweet_url


# --- Main Orchestration Loop ---

def run_due_posts(dry_run: bool = False):
    if not SCHEDULE_FILE.exists():
        logger.error("Schedule export file %s not found!", SCHEDULE_FILE)
        return

    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    now_utc = datetime.now(timezone.utc)
    logger.info("Runner triggered at %s UTC", now_utc.isoformat())

    posts = data.get("posts", [])
    accounts = load_accounts() or data.get("accounts", {})
    updated_count = 0

    for post in posts:
        if post.get("status") != "scheduled":
            continue

        scheduled_time_str = post.get("scheduled_for")
        if not scheduled_time_str:
            continue

        scheduled_dt = datetime.fromisoformat(scheduled_time_str)
        if scheduled_dt > now_utc:
            # Not due yet
            continue

        post_id = post["id"]
        provider = post["provider"]
        acct_id = post.get("social_account_id")
        account = accounts.get(acct_id)

        logger.info(
            "Found due post %s for provider %s scheduled at %s",
            post_id,
            provider,
            scheduled_time_str,
        )

        if not account:
            logger.error("No credentials found for account %s (post %s)", acct_id, post_id)
            continue

        if dry_run:
            logger.info("[DRY RUN] Would publish post %s to %s", post_id, provider)
            continue

        s3_uri = post.get("source_file_path", "")
        s3_key = extract_s3_key(s3_uri)
        tmp_clip = None

        try:
            if provider == "instagram":
                presigned_url = generate_presigned_url(s3_key)
                ext_id, ext_url = publish_instagram(account, post, presigned_url)
            else:
                tmp_clip = download_clip_to_temp(s3_key)
                if provider == "youtube":
                    ext_id, ext_url = publish_youtube(account, post, tmp_clip)
                elif provider == "facebook":
                    ext_id, ext_url = publish_facebook(account, post, tmp_clip)
                elif provider == "linkedin":
                    ext_id, ext_url = publish_linkedin(account, post, tmp_clip)
                elif provider == "twitter":
                    ext_id, ext_url = publish_twitter(account, post, tmp_clip)
                else:
                    logger.warning("Unsupported provider %s", provider)
                    continue

            post["status"] = "published"
            post["published_at"] = datetime.now(timezone.utc).isoformat()
            post["external_post_id"] = ext_id
            post["external_url"] = ext_url
            updated_count += 1
            logger.info("Successfully published post %s to %s! URL: %s", post_id, provider, ext_url)

        except Exception as e:
            logger.exception("Failed to publish post %s to %s: %s", post_id, provider, e)
            post["last_error"] = str(e)
            post["failed_at"] = datetime.now(timezone.utc).isoformat()

        finally:
            if tmp_clip and tmp_clip.exists():
                try:
                    tmp_clip.unlink()
                except Exception:
                    pass

    if updated_count > 0:
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Updated %d posts in %s", updated_count, SCHEDULE_FILE)
    else:
        logger.info("No due posts published in this cycle.")


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    run_due_posts(dry_run=is_dry)
