"""Format-only validation for Monero mainnet addresses.

This checks length, prefix, and alphabet only. It does not validate Monero's
block-base58 encoding or Keccak-256 checksum; the standard library provides
SHA3-256, which is not Keccak-256.
"""

from typing import Final

_ADDRESS_ALPHABET: Final[str] = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_MAINNET_LENGTHS: Final[dict[int, frozenset[str]]] = {
    95: frozenset("48"),
    106: frozenset("4"),
}


def validate_address(addr: str) -> tuple[bool, str]:
    """Check Monero mainnet address format, without cryptographic validation."""
    if len(addr) not in _MAINNET_LENGTHS:
        return False, f"length must be 95 or 106 characters (got {len(addr)})"

    invalid_character = next(
        ((position, character) for position, character in enumerate(addr, 1)
         if character not in _ADDRESS_ALPHABET),
        None,
    )
    if invalid_character is not None:
        position, character = invalid_character
        return False, f"invalid character {character!r} at position {position}"

    prefix = addr[0]
    if prefix in "9B":
        return False, "testnet address"
    if prefix not in _MAINNET_LENGTHS[len(addr)]:
        return False, f"bad prefix {prefix!r} for {len(addr)}-character address"

    return True, "valid Monero mainnet address format"
