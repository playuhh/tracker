import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from portfolio import (
    AccessComplianceBlocked,
    DomainRequestGovernor,
    PropertyMode,
    RequestBudgetExceeded,
    load_registry,
    probe_registry,
    public_portfolio_enabled,
    validate_registry,
)
from scraper import post_json


def registry_entry(**updates):
    entry = {
        "key": "property-a", "public_label": "Building A", "name": "Private A",
        "public_url": "https://example.invalid/a", "source": {"page_id": "private-value"},
        "city": "Private City", "neighborhood": "Private Area", "provider": "veris",
        "adapter_version": "veris_wp_ajax_v1", "enabled": True,
        "last_contract_verification": "2026-07-20", "compliance_status": "approved_research",
        "capabilities": {"market": True, "traits": False}, "mode": "market_only",
    }
    entry.update(updates)
    return entry


class RegistryTest(unittest.TestCase):
    def test_valid_registry_defaults_new_property_to_market_only(self):
        config = validate_registry({"properties": [registry_entry()]})[0]
        self.assertEqual(config.mode, PropertyMode.MARKET_ONLY)
        self.assertFalse(config.capabilities.traits)

    def test_missing_fields_and_private_source_fail_clearly(self):
        entry = registry_entry()
        del entry["city"]
        with self.assertRaisesRegex(ValueError, "city"):
            validate_registry({"properties": [entry]})
        with self.assertRaisesRegex(ValueError, "private source"):
            validate_registry({"properties": [registry_entry(source={})]})

    def test_duplicate_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate property key"):
            validate_registry({"properties": [registry_entry(), registry_entry()]})

    def test_flags_must_be_real_booleans(self):
        with self.assertRaisesRegex(ValueError, "enabled flag"):
            validate_registry({"properties": [registry_entry(enabled="false")]})
        with self.assertRaisesRegex(ValueError, "capability flags"):
            validate_registry({"properties": [registry_entry(
                capabilities={"market": "yes", "traits": False}
            )]})

    def test_invalid_mode_and_unverified_trait_catalog_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid mode"):
            validate_registry({"properties": [registry_entry(mode="guessed")]})
        with self.assertRaisesRegex(ValueError, "property-specific catalog"):
            validate_registry({"properties": [registry_entry(
                mode="trait_enriched", capabilities={"market": True, "traits": True}
            )]})

    def test_trait_catalog_must_be_bound_to_existing_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaisesRegex(ValueError, "catalog is missing"):
                validate_registry({"properties": [registry_entry(
                    mode="trait_enriched", capabilities={"market": True, "traits": True},
                    traits_catalog_path="catalog.csv",
                )]}, base)

    def test_one_trait_catalog_cannot_be_reused_for_another_property(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "catalog.csv").write_text("unit_id\n", encoding="utf-8")
            first = registry_entry(
                mode="trait_enriched", capabilities={"market": True, "traits": True},
                traits_catalog_path="catalog.csv",
            )
            second = {**first, "key": "property-b", "public_label": "Building B"}
            with self.assertRaisesRegex(ValueError, "cannot be reused"):
                validate_registry({"properties": [first, second]}, base)

    def test_legacy_environment_remains_supported(self):
        config = load_registry(environ={"RENTAL_BUILDING_PAGE_ID": "private-value"})[0]
        self.assertEqual(config.public_label, "Building A")
        self.assertEqual(config.mode, PropertyMode.TRAIT_ENRICHED)

    def test_missing_registry_has_explicit_error(self):
        with self.assertRaisesRegex(RuntimeError, "RENTAL_PROPERTY_REGISTRY"):
            load_registry(environ={})

    def test_probe_is_no_write_and_does_not_claim_compatibility(self):
        config = validate_registry({"properties": [registry_entry()]})
        result = probe_registry(config)[0]
        self.assertEqual(result["status"], "not_tested")
        self.assertEqual(result["request_count"], 0)

    def test_public_portfolio_gate_is_explicit_and_off_by_default(self):
        self.assertFalse(public_portfolio_enabled({}))
        self.assertTrue(public_portfolio_enabled(
            {"ALLOW_PUBLIC_PORTFOLIO_REPORT": "I_HAVE_PUBLICATION_PERMISSION"}
        ))


class GovernorTest(unittest.TestCase):
    def test_total_and_property_budgets_are_domain_wide(self):
        governor = DomainRequestGovernor(max_requests=2, max_properties=1,
                                          fixed_interval_seconds=0)
        governor.before_request("a")
        governor.record(200, .1, 10)
        governor.before_request("a")
        governor.record(200, .2, 20)
        with self.assertRaises(RequestBudgetExceeded):
            governor.before_request("a")
        self.assertEqual(governor.metrics.total_requests, 2)
        self.assertEqual(governor.metrics.downloaded_bytes, 30)

    def test_retry_budget_is_bounded(self):
        governor = DomainRequestGovernor(max_retries=1)
        governor.record_retry()
        with self.assertRaises(RequestBudgetExceeded):
            governor.record_retry()

    def test_access_statuses_and_challenge_open_circuit(self):
        for status, body in ((401, b""), (403, b""), (429, b""), (200, b"captcha")):
            with self.subTest(status=status, body=body):
                governor = DomainRequestGovernor(fixed_interval_seconds=0)
                governor.before_request("a")
                with self.assertRaises(AccessComplianceBlocked):
                    governor.record(status, .1, len(body), body)
                with self.assertRaises(AccessComplianceBlocked):
                    governor.before_request("b")

    def test_fixed_interval_serializes_requests_without_randomness(self):
        state = {"now": 0.0, "sleeps": []}
        def clock(): return state["now"]
        def sleep(seconds):
            state["sleeps"].append(seconds)
            state["now"] += seconds
        governor = DomainRequestGovernor(fixed_interval_seconds=2, clock=clock, sleep=sleep)
        governor.before_request("a")
        governor.record(200, .1, 1)
        governor.before_request("a")
        self.assertEqual(state["sleeps"], [2.0])

    def test_transport_retries_5xx_but_not_access_denials(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"ok": true}'

        server_error = HTTPError("https://example.invalid", 503, "temporary", {}, io.BytesIO())
        governor = DomainRequestGovernor(max_retries=1, fixed_interval_seconds=0)
        with patch("scraper.urlopen", side_effect=[server_error, Response()]) as opener, \
             patch("scraper.time.sleep"):
            self.assertTrue(post_json({"action": "test"}, governor, "a")["ok"])
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(governor.metrics.retries, 1)

        denied = HTTPError("https://example.invalid", 403, "denied", {}, io.BytesIO())
        governor = DomainRequestGovernor(fixed_interval_seconds=0)
        with patch("scraper.urlopen", side_effect=denied) as opener:
            with self.assertRaises(AccessComplianceBlocked):
                post_json({"action": "test"}, governor, "a")
        self.assertEqual(opener.call_count, 1)


if __name__ == "__main__":
    unittest.main()
