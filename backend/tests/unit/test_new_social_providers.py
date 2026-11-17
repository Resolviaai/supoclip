import pytest
from unittest.mock import MagicMock

from src.social.facebook import FacebookProvider
from src.social.linkedin import LinkedInProvider
from src.social.twitter import TwitterProvider
from src.social.registry import get_provider, PROVIDER_SETUP_HINTS, _PROVIDER_CLASSES


def test_registry_contains_linkedin_and_twitter_and_facebook():
    assert "linkedin" in _PROVIDER_CLASSES
    assert "twitter" in _PROVIDER_CLASSES
    assert "facebook" in _PROVIDER_CLASSES
    assert "linkedin" in PROVIDER_SETUP_HINTS
    assert "twitter" in PROVIDER_SETUP_HINTS
    assert "facebook" in PROVIDER_SETUP_HINTS


def test_facebook_provider_initialization():
    mock_config = MagicMock()
    mock_config.facebook_app_id = "test-fb-app-id"
    mock_config.facebook_app_secret = "test-fb-app-secret"
    mock_config.frontend_url = "https://app.supoclip.com"

    provider = FacebookProvider(config=mock_config)
    assert provider.name == "facebook"
    assert provider.uses_pkce is False
    assert provider.is_configured is True

    url = provider.build_authorize_url(
        state="fb-state-123",
        redirect_uri="https://app.supoclip.com/api/social/callback/facebook",
    )
    assert "https://www.facebook.com/v21.0/dialog/oauth" in url
    assert "client_id=test-fb-app-id" in url
    assert "state=fb-state-123" in url
    assert "pages_manage_posts" in url


def test_linkedin_provider_initialization():
    mock_config = MagicMock()
    mock_config.linkedin_client_id = "test-client-id"
    mock_config.linkedin_client_secret = "test-client-secret"
    mock_config.frontend_url = "https://app.supoclip.com"

    provider = LinkedInProvider(config=mock_config)
    assert provider.name == "linkedin"
    assert provider.uses_pkce is False
    assert provider.is_configured is True

    url = provider.build_authorize_url(
        state="test-state-123",
        redirect_uri="https://app.supoclip.com/api/social/callback/linkedin",
    )
    assert "https://www.linkedin.com/oauth/v2/authorization" in url
    assert "client_id=test-client-id" in url
    assert "state=test-state-123" in url
    assert "openid" in url
    assert "w_member_social" in url


def test_twitter_provider_initialization():
    mock_config = MagicMock()
    mock_config.twitter_client_id = "test-twitter-id"
    mock_config.twitter_client_secret = "test-twitter-secret"
    mock_config.frontend_url = "https://app.supoclip.com"

    provider = TwitterProvider(config=mock_config)
    assert provider.name == "twitter"
    assert provider.uses_pkce is True
    assert provider.is_configured is True

    url = provider.build_authorize_url(
        state="twitter-state-456",
        redirect_uri="https://app.supoclip.com/api/social/callback/twitter",
        code_challenge="mock-code-challenge",
    )
    assert "https://twitter.com/i/oauth2/authorize" in url
    assert "client_id=test-twitter-id" in url
    assert "code_challenge=mock-code-challenge" in url
    assert "code_challenge_method=S256" in url
