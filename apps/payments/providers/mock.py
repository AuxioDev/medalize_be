import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse

from .base import PaymentProvider, ProviderOrder


class MockCardProvider(PaymentProvider):
    """Temporary stand-in for PayriffProvider while the real Payriff
    integration isn't wired up yet — see apps.payments.service.get_provider,
    the single seam that selects between them via settings.PAYMENT_PROVIDER.
    Same interface as PayriffProvider, so switching back later is a one-line
    settings change with nothing else in views.py/service.py to touch.

    Makes no external API calls. ``create_order`` points ``payment_url`` at
    a same-backend HTML page (MockCheckoutView, apps/payments/views.py) with
    a cosmetic card-entry form — opened in the external browser exactly like
    a real hosted-checkout page would be. Submitting that form marks the
    underlying Payment/SubscriptionPayment row paid directly (via the same
    handle_webhook_ping() every real webhook goes through) and redirects to
    the same approve_url a real provider would use, so the mobile app's
    existing app-resume status recheck picks it up with zero client-side
    changes.
    """

    name = 'mock'

    def create_order(self, *, amount, currency, description, approve_url, cancel_url, decline_url):
        order_id = f'mock-{uuid.uuid4().hex}'
        base = settings.BACKEND_BASE_URL.rstrip('/')
        query = urlencode({
            'order_id': order_id,
            'amount': amount,
            'currency': currency,
            'description': description,
            'approve_url': approve_url,
            'cancel_url': cancel_url,
        })
        payment_url = f'{base}{reverse("mock-checkout")}?{query}'
        return ProviderOrder(order_id=order_id, payment_url=payment_url, session_id='')

    def check_status(self, provider_order_id):
        # No external state to query — see the class docstring. The only
        # real path to "paid" is MockCheckoutView.post marking the row
        # directly; this exists so a manual re-sync (or handle_webhook_ping
        # being called a second time) doesn't hard-fail for lack of an
        # implementation.
        return 'paid'

    def refund_order(self, provider_order_id, amount):
        # No external state to touch, and nothing that can fail — a refund
        # against the mock provider always "succeeds". Exists purely so
        # apps.payments.service.refund_payment has the same provider-
        # agnostic call to make regardless of PAYMENT_PROVIDER.
        return None
