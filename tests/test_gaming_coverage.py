# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Gaming coverage admission and refusal tests.

These tests verify that the gaming integration honestly documents its admission
status and refuses silent downgrade from FULL to fallback coverage.
"""

import unittest
from pathlib import Path


class TestGamingCoverageAdmission(unittest.TestCase):
    """Verify gaming coverage admission is documented honestly."""

    def test_refuses_silent_downgrade(self):
        """Gaming contract must document NO_DEPENDABLE_ROUTE admission."""
        contract_path = Path("docs/gaming-integration-contract.md")
        self.assertTrue(contract_path.exists(), "gaming-integration-contract.md must exist")

        content = contract_path.read_text()

        # Must document the admission decision
        self.assertIn("NO_DEPENDABLE_ROUTE", content)
        self.assertIn("FULL gaming coverage", content)
        self.assertIn("NOT been demonstrated", content)

        # Must distinguish FULL from fallback
        self.assertIn("conservative fallback", content.lower())
        self.assertIn("conservative session inhibition", content.lower())

        # Must not claim FULL coverage
        self.assertNotIn("FULL gaming safety", content)
        self.assertNotIn("full support", content.lower())

    def test_documents_latency_objectives_not_guarantees(self):
        """Gaming contract must frame latency as objectives, not guarantees."""
        contract_path = Path("docs/gaming-integration-contract.md")
        content = contract_path.read_text()

        # Must mention latency objectives
        self.assertIn("1 second", content)
        self.assertIn("5 second", content)

        # Must frame as objectives, not guarantees
        self.assertIn("engineering objectives", content.lower())
        self.assertIn("not hard real-time", content.lower())
        self.assertIn("not zero game-start overlap", content.lower())

    def test_documents_manual_pause_independence(self):
        """Gaming contract must document manual pause independence."""
        contract_path = Path("docs/gaming-integration-contract.md")
        content = contract_path.read_text()

        # Must document manual pause interaction
        self.assertIn("manual pause", content.lower())
        self.assertIn("independent", content.lower())
        self.assertIn("cannot bypass", content.lower())

    def test_documents_fallback_only_status(self):
        """Gaming contract must document fallback-only NO-GO status."""
        contract_path = Path("docs/gaming-integration-contract.md")
        content = contract_path.read_text()

        # Must document NO-GO status
        self.assertIn("NO-GO", content)
        self.assertIn("reduced-function", content.lower())
        self.assertIn("pending owner decision", content.lower())

    def test_documents_unknowns_and_limitations(self):
        """Gaming contract must document unknowns and limitations."""
        contract_path = Path("docs/gaming-integration-contract.md")
        content = contract_path.read_text()

        # Must document key limitations
        self.assertIn("session bus requirement", content.lower())
        self.assertIn("lingering teardown", content.lower())
        self.assertIn("monitor loss", content.lower())
        self.assertIn("uncovered workloads", content.lower())

    def test_readme_documents_admission_status(self):
        """README must document gaming admission status."""
        readme_path = Path("README.md")
        content = readme_path.read_text()

        # Must mention admission status
        self.assertIn("conservative fallback", content.lower())
        self.assertIn("NOT been demonstrated", content)
        self.assertIn("NO_DEPENDABLE_ROUTE", content)

        # Must reference the contract
        self.assertIn("docs/gaming-integration-contract.md", content)

    def test_security_md_documents_gaming_limitations(self):
        """SECURITY.md must document gaming coverage limitations."""
        security_path = Path("SECURITY.md")
        content = security_path.read_text()

        # Must document gaming limitations
        self.assertIn("conservative fallback", content.lower())
        self.assertIn("NO_DEPENDABLE_ROUTE", content)
        self.assertIn("NOT been demonstrated", content)

        # Must reference the contract
        self.assertIn("docs/gaming-integration-contract.md", content)

    def test_architecture_documents_gaming_module(self):
        """Architecture doc must document gaming module and admission."""
        arch_path = Path("docs/architecture.md")
        content = arch_path.read_text()

        # Must document gaming observation module
        self.assertIn("sparehash/gaming.py", content)
        self.assertIn("gaming observation", content.lower())

        # Must document admission status
        self.assertIn("NO_DEPENDABLE_ROUTE", content)
        self.assertIn("conservative session inhibition", content.lower())

        # Must document latency objectives
        self.assertIn("1 second", content)
        self.assertIn("5 second", content)


if __name__ == "__main__":
    unittest.main()
