class TaxPolicy:
    """Port: a tax rate per region. Implementations may return 0.0 for untaxed regions."""

    def rate(self, region: str) -> float:
        raise NotImplementedError
