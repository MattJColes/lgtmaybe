"""Link persistence. `mark_redeemed` is a conditional update, so it is already
idempotent: a second call on an already-redeemed row affects no rows."""


class Links:
    def insert(self, link): ...

    def get(self, link_id): ...

    def mark_redeemed(self, link):
        """UPDATE links SET redeemed_at = now() WHERE id = ? AND redeemed_at IS NULL"""
        ...


links = Links()
