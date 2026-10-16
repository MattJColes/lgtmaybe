class StripeClient:
    """Adapter: payments through the Stripe SDK. Vendor code lives under billing/adapters/."""

    def charge(self, cents: int) -> None:
        raise NotImplementedError
