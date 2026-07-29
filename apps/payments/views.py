import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.models import Appointment
from apps.users.permissions import IsPatient

from .models import Payment
from .serializers import PaymentSerializer
from .service import get_or_create_payment, handle_webhook_ping, payments_enabled

logger = logging.getLogger(__name__)


class AppointmentPaymentView(APIView):
    """GET is open to the appointment's doctor or patient (read-only status
    is useful to both — mirrors apps.prescriptions.views
    .AppointmentPrescriptionView); POST — initiating a payment — is
    patient-only, since only the patient pays. Same 404-not-403 ownership
    idiom throughout, so the existence of another user's appointment is
    never revealed."""

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsPatient()]
        return [IsAuthenticated()]

    def get(self, request, pk):
        try:
            appointment = Appointment.objects.select_related('doctor', 'patient', 'dependent').get(pk=pk)
        except Appointment.DoesNotExist:
            raise NotFound()
        if request.user not in (appointment.doctor, appointment.patient):
            raise NotFound()
        try:
            payment = appointment.payment
        except Payment.DoesNotExist:
            raise NotFound()
        return Response(PaymentSerializer(payment).data)

    def post(self, request, pk):
        if not payments_enabled():
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            appointment = Appointment.objects.select_related('doctor', 'patient').get(
                pk=pk, patient=request.user,
            )
        except Appointment.DoesNotExist:
            raise NotFound()
        payment = get_or_create_payment(appointment)
        # 200 rather than 201: get_or_create_payment is an idempotent
        # upsert-and-fetch (repeat calls return the same pending/paid
        # payment instead of erroring or duplicating), so there is no single
        # "this always creates a new resource" moment to hang 201 off of.
        return Response(PaymentSerializer(payment).data, status=status.HTTP_200_OK)


class PayriffWebhookView(APIView):
    """Payriff calls this from its own servers — no JWT is available or
    expected, hence no authentication at all (AllowAny + authentication_
    classes = [], matching the convention documented for external-service
    webhooks).

    Deliberately reads only enough of the body to find an order id — the
    exact webhook payload shape (and its signature scheme) could not be
    confirmed from any source. See service.handle_webhook_ping: whatever
    status the body claims is never trusted, only used as a nudge to ask
    Payriff directly via check_status().
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        order_id = (
            data.get('transactionId')
            or data.get('orderId')
            or data.get('order_id')
            or data.get('id')
        )
        if not order_id:
            logger.warning('Payriff webhook received with no recognizable order id: %r', data)
            return Response(status=status.HTTP_200_OK)

        try:
            handle_webhook_ping(str(order_id))
        except Exception:
            # Always answer 200 fast — providers typically retry on
            # non-200 — internal errors are logged, never surfaced as 500.
            logger.exception('Unhandled error processing Payriff webhook for order %s', order_id)

        return Response(status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class MockCheckoutView(View):
    """The fake hosted-checkout page for MockCardProvider (see
    apps.payments.providers.mock) — the temporary stand-in for Payriff's
    real checkout while that integration isn't wired up (see
    apps.payments.service.get_provider). AllowAny/no-DRF, matching
    PayriffReturnView below: this page is opened in the user's external
    browser exactly like the real Payriff page would be, so there is no
    session/JWT (or same-site cookie) available here.

    csrf_exempt is deliberate, not an oversight: this view stands in for a
    page that, for the real provider, is hosted entirely outside this
    Django app (Payriff's own domain) — nothing about Django's own
    same-origin form protection is meaningful for it, the same reason
    PayriffWebhookView below carries no CSRF concern either. It's still
    safe to expose: POST only ever flips one Payment/SubscriptionPayment
    row to "paid" for a caller-supplied order_id via the exact same
    handle_webhook_ping() a real webhook goes through, and the redirect
    target is independently restricted to our own backend host (see post()
    below) — there's no state here an attacker gains anything from forging.

    GET renders the card-entry form (all fields are cosmetic — nothing is
    validated as a real card); POST is the "Pay" submission, which marks
    the underlying Payment/SubscriptionPayment row paid via the exact same
    handle_webhook_ping() a real Payriff webhook would trigger (see that
    function's shared-order-id-space routing between apps.payments and
    apps.subscriptions), then redirects to the same approve_url a real
    provider would use — the mobile app's existing app-resume status
    recheck picks up the new status with no client-side changes at all.
    """

    def get(self, request):
        params = {
            key: escape(request.GET.get(key, ''))
            for key in ('order_id', 'amount', 'currency', 'description', 'approve_url', 'cancel_url')
        }
        return HttpResponse(_mock_checkout_html(**params))

    def post(self, request):
        order_id = request.POST.get('order_id', '')
        approve_url = request.POST.get('approve_url', '')
        if order_id:
            try:
                handle_webhook_ping(order_id)
            except Exception:
                logger.exception('Mock checkout confirmation failed for order %s', order_id)
        # Only ever redirect back to our own backend (where every real
        # approve_url this view is ever given points, see
        # MockCardProvider.create_order) — never follow an arbitrary
        # attacker-supplied redirect target.
        base = settings.BACKEND_BASE_URL.rstrip('/')
        if approve_url and approve_url.startswith(base):
            return HttpResponseRedirect(approve_url)
        return HttpResponse(status=204)


def _mock_checkout_html(order_id, amount, currency, description, approve_url, cancel_url):
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Medalize — Payment</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background:#f5f6fa; margin:0; padding:24px; color:#111827; }}
  .card {{ max-width:380px; margin:32px auto; background:#fff; border-radius:16px;
           padding:28px; box-shadow:0 4px 20px rgba(0,0,0,.08); }}
  .badge {{ display:inline-block; background:#fef3c7; color:#92400e; font-size:12px;
            font-weight:600; padding:4px 10px; border-radius:999px; margin-bottom:16px; }}
  h1 {{ font-size:17px; margin:0 0 4px; font-weight:600; }}
  .amount {{ font-size:28px; font-weight:700; margin:8px 0 22px; }}
  label {{ display:block; font-size:13px; color:#555; margin:14px 0 4px; }}
  input {{ width:100%; box-sizing:border-box; padding:10px 12px; border:1px solid #ddd;
           border-radius:8px; font-size:15px; }}
  input:focus {{ outline:none; border-color:#4f46e5; }}
  .row {{ display:flex; gap:10px; }}
  .row > div {{ flex:1; }}
  button {{ width:100%; margin-top:22px; padding:13px; background:#4f46e5; color:#fff;
            border:none; border-radius:10px; font-size:15px; font-weight:600; cursor:pointer; }}
  a.cancel {{ display:block; text-align:center; margin-top:14px; color:#888; font-size:13px;
              text-decoration:none; }}
</style></head>
<body>
  <div class="card">
    <span class="badge">TEST PAYMENT</span>
    <h1>{description}</h1>
    <div class="amount">{amount} {currency}</div>
    <form method="post">
      <input type="hidden" name="order_id" value="{order_id}">
      <input type="hidden" name="approve_url" value="{approve_url}">
      <label>Card number</label>
      <input inputmode="numeric" maxlength="19" placeholder="4111 1111 1111 1111" required>
      <div class="row">
        <div><label>Expiry</label><input maxlength="5" placeholder="MM/YY" required></div>
        <div><label>CVC</label><input inputmode="numeric" maxlength="3" placeholder="123" required></div>
      </div>
      <label>Cardholder name</label>
      <input placeholder="Name on card" required>
      <button type="submit">Pay {amount} {currency}</button>
    </form>
    <a class="cancel" href="{cancel_url}">Cancel</a>
  </div>
</body></html>'''


class PayriffReturnView(View):
    """Plain Django View (not DRF) — a static, non-API browser page Payriff
    redirects the user's external browser to after checkout. NOT a source of
    truth for payment status (that's the webhook + check_status only, see
    service.py) — this page does no confirmation logic at all, it only
    bounces the browser back into the app.

    Auto-redirects into the app via the `medalize://` custom URL scheme
    (registered in ios/Runner/Info.plist's CFBundleURLTypes and
    android/app/.../AndroidManifest.xml's second intent-filter) so the user
    doesn't have to switch back manually — opening the scheme alone brings
    the app to the foreground, which already triggers the existing
    AppLifecycleState.resumed-based status recheck in
    PaymentScreen/SubscriptionScreen, no deep-link parsing needed on the
    Dart side. The meta-refresh fires automatically in most mobile
    browsers; the visible button is the fallback for the ones that require
    a user gesture before following a non-http(s) redirect."""

    _MESSAGES = {
        'en': 'Payment processed. Returning to the app…',
        'ru': 'Оплата обработана. Возвращаемся в приложение…',
        'az': 'Ödəniş həyata keçirildi. Tətbiqə qayıdılır…',
        'tr': 'Ödeme işlendi. Uygulamaya dönülüyor…',
        'fr': 'Paiement traité. Retour à l’application…',
        'zh': '支付已处理。正在返回应用程序…',
    }
    _BUTTON_LABELS = {
        'en': 'Open Medalize',
        'ru': 'Открыть Medalize',
        'az': 'Medalize-i aç',
        'tr': 'Medalize’i Aç',
        'fr': 'Ouvrir Medalize',
        'zh': '打开 Medalize',
    }

    def get(self, request):
        lang = escape(request.GET.get('lang', 'en'))
        result = escape(request.GET.get('result', ''))
        message = self._MESSAGES.get(lang, self._MESSAGES['en'])
        button_label = self._BUTTON_LABELS.get(lang, self._BUTTON_LABELS['en'])
        deep_link = f'medalize://payment-return?result={result}&lang={lang}'
        html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0;url={deep_link}">
<title>Medalize</title></head>
<body style="font-family:-apple-system,sans-serif;text-align:center;padding-top:20vh;">
<p>{message}</p>
<a href="{deep_link}" style="display:inline-block;margin-top:18px;padding:12px 28px;
background:#4f46e5;color:#fff;border-radius:10px;text-decoration:none;font-weight:600;">
{button_label}</a>
</body></html>'''
        return HttpResponse(html)
