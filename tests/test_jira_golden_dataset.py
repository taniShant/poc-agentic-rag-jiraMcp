"""Tests for the deterministic Jira golden dataset generator."""

from __future__ import annotations

import unittest
from datetime import date

from scripts.jira.create_completed_golden_tickets import (
    SCENARIOS,
    generate_golden_tickets,
)


class JiraGoldenDatasetTests(unittest.TestCase):
    """Verify dataset size, uniqueness, coverage, and answer completeness."""

    def test_default_dataset_contains_100_unique_cases(self) -> None:
        """The default seed must contain exactly 100 stable external IDs."""
        tickets = generate_golden_tickets()
        self.assertEqual(len(tickets), 100)
        self.assertEqual(len({ticket.external_id for ticket in tickets}), 100)
        self.assertEqual(len({ticket.summary for ticket in tickets}), 100)

    def test_every_scenario_has_five_variants(self) -> None:
        """One hundred records must evenly cover all twenty service topics."""
        tickets = generate_golden_tickets()
        counts = {
            scenario.category: sum(
                ticket.category == scenario.category for ticket in tickets
            )
            for scenario in SCENARIOS
        }
        self.assertEqual(len(SCENARIOS), 20)
        self.assertTrue(all(count == 5 for count in counts.values()))

    def test_cases_have_dates_resolution_and_validation(self) -> None:
        """Every case must be closed after opening and contain golden evidence."""
        for ticket in generate_golden_tickets():
            self.assertLess(date.fromisoformat(ticket.opened_date), date.fromisoformat(ticket.completed_date))
            self.assertGreaterEqual(len(ticket.resolution_steps), 3)
            self.assertGreaterEqual(len(ticket.validation), 3)
            self.assertIn("APPROVED RESOLUTION", ticket.description())
            self.assertIn(ticket.external_id, ticket.resolution_comment())
            self.assertTrue(ticket.requester_email.endswith("@example.invalid"))

    def test_invalid_counts_are_rejected(self) -> None:
        """The seeder must not silently generate an unintended bulk size."""
        for count in (0, 101):
            with self.assertRaises(ValueError):
                generate_golden_tickets(count)


if __name__ == "__main__":
    unittest.main()

