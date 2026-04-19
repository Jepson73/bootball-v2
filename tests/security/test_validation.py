"""
tests/security/test_validation.py

Tests for input validation.
"""
import pytest

from src.security.validation import (
    sanitize_string,
    sanitize_for_html,
    sanitize_for_log,
    validate_fixture_id,
    validate_league_id,
    validate_market,
    validate_days_param,
    validate_limit_param,
    validate_team_name,
    validate_event_payload,
    ValidationResult,
)


class TestSanitizeString:
    def test_basic_string(self):
        assert sanitize_string("hello") == "hello"

    def test_removes_null_bytes(self):
        assert sanitize_string("hello\x00world") == "helloworld"

    def test_removes_control_chars(self):
        assert sanitize_string("hello\x07world") == "helloworld"

    def test_max_length(self):
        long_str = "a" * 300
        result = sanitize_string(long_str, max_length=255)
        assert len(result) == 255

    def test_none_input(self):
        assert sanitize_string(None) == ""


class TestSanitizeForHTML:
    def test_encodes_ampersand(self):
        assert sanitize_for_html("A & B") == "A &amp; B"

    def test_encodes_less_than(self):
        assert sanitize_for_html("<script>") == "&lt;script&gt;"

    def test_encodes_greater_than(self):
        assert sanitize_for_html("5 > 3") == "5 &gt; 3"

    def test_encodes_quotes(self):
        assert sanitize_for_html('"quotes"') == "&quot;quotes&quot;"
        assert sanitize_for_html("'single'") == "&#x27;single&#x27;"

    def test_none_input(self):
        assert sanitize_for_html(None) == ""


class TestSanitizeForLog:
    def test_removes_newlines(self):
        assert sanitize_for_log("hello\nworld") == "hello world"

    def test_removes_carriage_returns(self):
        assert sanitize_for_log("hello\rworld") == "helloworld"

    def test_removes_tabs(self):
        assert sanitize_for_log("hello\tworld") == "hello world"

    def test_log_injection_prevention(self):
        """Sanitize_for_log removes newlines, but validation should catch content."""
        # Note: sanitize_for_log removes newlines, it doesn't filter content
        # Validation via validate_team_name should catch malicious content
        malicious = "Team A\n2026-04-19 ADMIN: DELETE ALL DATA"
        sanitized = sanitize_for_log(malicious)
        # Newlines are removed
        assert "\n" not in sanitized

    def test_none_input(self):
        assert sanitize_for_log(None) == ""


class TestValidateFixtureId:
    def test_valid_id(self):
        result = validate_fixture_id(123)
        assert result.valid is True
        assert result.sanitized == 123

    def test_string_id(self):
        result = validate_fixture_id("456")
        assert result.valid is True
        assert result.sanitized == 456

    def test_none_rejected(self):
        result = validate_fixture_id(None)
        assert result.valid is False
        assert "required" in result.error.lower()

    def test_non_integer_rejected(self):
        result = validate_fixture_id("abc")
        assert result.valid is False
        assert "integer" in result.error.lower()

    def test_negative_rejected(self):
        result = validate_fixture_id(-1)
        assert result.valid is False

    def test_out_of_range(self):
        result = validate_fixture_id(999999999)
        assert result.valid is False


class TestValidateLeagueId:
    def test_valid_id(self):
        result = validate_league_id(39)
        assert result.valid is True

    def test_none_rejected(self):
        result = validate_league_id(None)
        assert result.valid is False


class TestValidateMarket:
    def test_valid_markets(self):
        for market in ["btts", "h2h", "ou25", "ou15"]:
            result = validate_market(market)
            assert result.valid is True, f"Market {market} should be valid"

    def test_unknown_market_rejected(self):
        result = validate_market("unknown_market")
        assert result.valid is False
        assert "unknown market" in result.error.lower()

    def test_uppercase_normalized(self):
        result = validate_market("BTTS")
        assert result.valid is True
        assert result.sanitized == "btts"

    def test_invalid_format_rejected(self):
        result = validate_market("123-invalid")
        assert result.valid is False

    def test_none_rejected(self):
        result = validate_market(None)
        assert result.valid is False


class TestValidateDaysParam:
    def test_valid_integer(self):
        result = validate_days_param(7)
        assert result.valid is True
        assert result.sanitized == 7

    def test_string_all(self):
        result = validate_days_param("all")
        assert result.valid is True
        assert result.sanitized == "all"

    def test_string_integer(self):
        result = validate_days_param("3")
        assert result.valid is True
        assert result.sanitized == 3

    def test_zero_rejected(self):
        result = validate_days_param(0)
        assert result.valid is False

    def test_negative_rejected(self):
        result = validate_days_param(-1)
        assert result.valid is False

    def test_too_large_rejected(self):
        result = validate_days_param(500)
        assert result.valid is False


class TestValidateLimitParam:
    def test_valid_limit(self):
        result = validate_limit_param(50)
        assert result.valid is True
        assert result.sanitized == 50

    def test_none_returns_default(self):
        result = validate_limit_param(None)
        assert result.valid is True
        assert result.sanitized == 100  # default

    def test_exceeds_max_rejected(self):
        result = validate_limit_param(2000, max_limit=1000)
        assert result.valid is False


class TestValidateTeamName:
    def test_valid_name(self):
        result = validate_team_name("Manchester United")
        assert result.valid is True

    def test_sanitizes_html(self):
        result = validate_team_name("<script>alert(1)</script>Team")
        assert result.valid is False
        assert "suspicious" in result.error.lower()

    def test_strips_whitespace(self):
        result = validate_team_name("  Team Name  ")
        assert result.valid is True
        assert result.sanitized == "Team Name"

    def test_empty_rejected(self):
        result = validate_team_name("")
        assert result.valid is False

    def test_whitespace_only_rejected(self):
        result = validate_team_name("   ")
        assert result.valid is False


class TestValidateEventPayload:
    def test_valid_payload(self):
        result = validate_event_payload({"key": "value"})
        assert result.valid is True

    def test_none_rejected(self):
        result = validate_event_payload(None)
        assert result.valid is False

    def test_non_dict_rejected(self):
        result = validate_event_payload(["list", "not", "dict"])
        assert result.valid is False
