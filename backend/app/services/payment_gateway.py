"""Adapter for the external Payment Gateway (FR-14, FR-16, NFR-10).

The prototype uses a sandbox gateway that behaves like Stripe test mode:
the client only ever sends a payment-method *token*, never a card number.

Test tokens:
    pm_card_visa         -> succeeds
    pm_card_mastercard   -> succeeds
    pm_card_declined     -> declined
    pm_card_timeout      -> gateway does not respond
"""
import secrets


class GatewayTimeout(Exception):
    pass


SUCCESS_TOKENS = {"pm_card_visa", "pm_card_mastercard", "pm_wallet_applepay"}


def charge(token: str, amount: float, description: str) -> dict:
    if token == "pm_card_timeout":
        raise GatewayTimeout("Payment gateway did not respond")
    if token in SUCCESS_TOKENS:
        return {"status": "succeeded", "gateway_ref": "ch_" + secrets.token_hex(8)}
    return {"status": "declined", "gateway_ref": "ch_" + secrets.token_hex(8)}


def refund(gateway_ref: str, amount: float) -> dict:
    return {"status": "succeeded", "gateway_ref": "re_" + secrets.token_hex(8)}
