"""Tests for format-only Monero address validation."""

import unittest

from sparehash.wallet import validate_address


class ValidateAddressTests(unittest.TestCase):
    def test_accepts_supported_mainnet_formats(self) -> None:
        cases = (
            "4" + "1" * 94,
            "8" + "1" * 94,
            "4" + "1" * 105,
        )
        for address in cases:
            with self.subTest(length=len(address), prefix=address[0]):
                self.assertEqual(validate_address(address), (True, "valid Monero mainnet address format"))

    def test_rejects_invalid_formats_with_specific_reasons(self) -> None:
        cases = (
            ("", "length"),
            ("4" + "1" * 93, "length"),
            ("4" + "1" * 94 + "1", "length"),
            ("9" + "1" * 94, "testnet address"),
            ("B" + "1" * 94, "testnet address"),
            ("5" + "1" * 94, "bad prefix"),
            ("8" + "1" * 105, "bad prefix"),
            ("4" + "1" * 46 + "0" + "1" * 47, "position 48"),
            ("4" + "1" * 46 + "O" + "1" * 47, "position 48"),
            ("4" + "1" * 46 + "I" + "1" * 47, "position 48"),
            ("4" + "1" * 46 + "l" + "1" * 47, "position 48"),
            ("4" + "1" * 46 + " " + "1" * 47, "position 48"),
        )
        for address, reason in cases:
            with self.subTest(reason=reason, length=len(address)):
                valid, message = validate_address(address)
                self.assertFalse(valid)
                self.assertIn(reason, message)


if __name__ == "__main__":
    _ = unittest.main()
