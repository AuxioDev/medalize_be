from dataclasses import dataclass


@dataclass
class ProviderOrder:
    """Return value of PaymentProvider.create_order — deliberately a plain,
    provider-agnostic shape (not a Payriff-specific object) so service.py and
    the views never need to know which provider produced it."""

    order_id: str
    payment_url: str
    session_id: str = ''


class PaymentProvider:
    """Provider-agnostic payment gateway interface.

    ``PayriffProvider`` (providers/payriff.py) is the only implementation
    today, but views.py/service.py must only ever talk to this interface —
    never import or reference Payriff directly — so a future second provider
    (or a Payriff API version bump) is a swap-in adapter, not a rewrite of
    the booking/payment flow.
    """

    def create_order(self, *, amount, currency, description, approve_url, cancel_url, decline_url):
        """Create a hosted-checkout order with the provider.

        Returns a ``ProviderOrder`` (order_id, payment_url, session_id).
        ``payment_url`` is the hosted page the client opens in an external
        browser for card entry; the provider redirects the browser back to
        ``approve_url``/``cancel_url``/``decline_url`` once the user is done.
        """
        raise NotImplementedError

    def check_status(self, provider_order_id):
        """Query the provider directly for the CURRENT status of an order.

        This is the source of truth for payment status — see the defensive
        webhook pattern in ``service.handle_webhook_ping``, which calls this
        instead of ever trusting a webhook request body directly. Returns one
        of ``Payment.STATUS_*``.
        """
        raise NotImplementedError
