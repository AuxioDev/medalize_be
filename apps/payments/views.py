import logging

from django.http import HttpResponse
from django.views import View
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


class PayriffReturnView(View):
    """Plain Django View (not DRF) — a static, non-API browser page Payriff
    redirects the user's external browser to after checkout. NOT a source of
    truth for payment status (that's the webhook + check_status only, see
    service.py) — this page does no confirmation logic at all, it is only
    visible for a fraction of a second before the user closes the browser or
    switches back to the app."""

    _MESSAGES = {
        'en': 'Payment processed. You can return to the app now.',
        'ru': 'Оплата обработана. Вы можете вернуться в приложение.',
        'az': 'Ödəniş həyata keçirildi. Tətbiqə qayıda bilərsiniz.',
        'tr': 'Ödeme işlendi. Uygulamaya dönebilirsiniz.',
        'fr': 'Paiement traité. Vous pouvez revenir à l’application.',
        'zh': '支付已处理。您现在可以返回应用程序。',
    }

    def get(self, request):
        lang = request.GET.get('lang', 'en')
        message = self._MESSAGES.get(lang, self._MESSAGES['en'])
        html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Medalize</title></head>'
            '<body style="font-family:sans-serif;text-align:center;padding-top:20vh;">'
            f'<p>{message}</p></body></html>'
        )
        return HttpResponse(html)
