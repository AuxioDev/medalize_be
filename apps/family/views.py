from django.db import transaction
from django.http import HttpResponse
from django.utils.html import escape
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.i18n import render_template
from apps.users.permissions import IsPatient

from .models import Dependent
from .serializers import DependentCreateSerializer, DependentSerializer
from .services import apply_consent_objection, consent_token_is_valid
from .throttles import DependentConsentRateThrottle


class DependentListCreateView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        dependents = (
            Dependent.objects.filter(managed_by=request.user, is_active=True)
            .order_by('first_name')
        )
        return Response(DependentSerializer(dependents, many=True).data)

    def post(self, request):
        serializer = DependentCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        dependent = serializer.save()
        return Response(DependentSerializer(dependent).data, status=status.HTTP_201_CREATED)


class DependentDetailView(APIView):
    permission_classes = [IsPatient]

    def _get(self, pk, user):
        try:
            return Dependent.objects.get(pk=pk, managed_by=user)
        except Dependent.DoesNotExist:
            raise NotFound()

    def get(self, request, pk):
        return Response(DependentSerializer(self._get(pk, request.user)).data)

    def patch(self, request, pk):
        dependent = self._get(pk, request.user)
        serializer = DependentCreateSerializer(
            dependent, data=request.data, partial=True, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        dependent = serializer.save()
        return Response(DependentSerializer(dependent).data)

    def delete(self, request, pk):
        # Soft delete: keeps historical appointments/medications/records
        # attached to this dependent from being orphaned (same pattern as
        # apps.medications.views.MedicationDetailView.delete).
        dependent = self._get(pk, request.user)
        dependent.is_active = False
        dependent.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


_SUPPORTED_PAGE_LANGS = {'en', 'ru', 'az', 'tr', 'fr', 'zh'}


def _page_lang(request):
    lang = request.GET.get('lang') or request.POST.get('lang') or 'en'
    return lang if lang in _SUPPORTED_PAGE_LANGS else 'en'


def _render_page(heading, body, note=None, form_html=''):
    """Minimal, dependency-free HTML shell for a public, no-login browser
    page — same plain-f-string-HttpResponse idiom as
    apps.payments.views._mock_checkout_html/PayriffReturnView.get (no
    template engine, no shared styling framework: this app has no other
    public HTML pages of its own to share one with, and payments' pages
    aren't a shared component either — each is self-contained). All
    interpolated text is escaped; only `form_html` (built exclusively by
    this module, never from request data) is inserted unescaped."""
    extra = f'<p class="note">{escape(note)}</p>' if note else ''
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Medalize</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background:#f5f6fa; margin:0; padding:24px; color:#111827; }}
  .card {{ max-width:420px; margin:32px auto; background:#fff; border-radius:16px;
           padding:28px; box-shadow:0 4px 20px rgba(0,0,0,.08); }}
  h1 {{ font-size:18px; margin:0 0 12px; font-weight:700; }}
  p {{ font-size:14px; line-height:1.5; color:#374151; margin:0 0 14px; }}
  .note {{ color:#6b7280; font-size:13px; }}
  button {{ width:100%; margin-top:10px; padding:13px; background:#ef4444; color:#fff;
            border:none; border-radius:10px; font-size:15px; font-weight:600; cursor:pointer; }}
</style></head>
<body>
  <div class="card">
    <h1>{escape(heading)}</h1>
    <p>{escape(body)}</p>
    {extra}
    {form_html}
  </div>
</body></html>'''


def _invalid_page(lang):
    tpl = render_template('dependent_consent_invalid_page', lang)
    return HttpResponse(_render_page(tpl['heading'], tpl['body']), status=status.HTTP_400_BAD_REQUEST)


def _display_name(user):
    return f'{user.first_name} {user.last_name}'.strip() or user.email


class DependentConsentRejectView(APIView):
    """No-login, token-based page an adult (18+) dependent's own
    contact_email links to (see apps.family.services.issue_consent_notice)
    so they can object to being managed as a dependent without needing an
    account of their own — the product decision this implements is
    "positively notify, don't gate": the account holder is never blocked
    from adding e.g. an elderly parent they actively care for, but an
    adult dependent must be told and given a real, no-login way out.

    GET renders a plain confirmation page and makes **no state change** —
    deliberately two-step rather than acting on the GET itself, so an
    email client's automatic link-prescanning/virus-scanning bot (which
    follows every link in an email, including GET links, without a human
    involved) can never trigger a real objection by itself. Only an actual
    form submission (POST) does.

    Registered with permission_classes=[AllowAny]/no authentication (there
    is no account to authenticate) and throttle_classes=
    [DependentConsentRateThrottle] (apps.family.throttles) so this still
    gets the same DEFAULT_THROTTLE_RATES-driven rate limiting every other
    public endpoint in the app uses (see apps.users.views.
    PasswordResetRequestView/PasswordResetRateThrottle for the pattern
    being reused) instead of hand-rolled cache bookkeeping.

    Returns a plain django.http.HttpResponse (not a DRF Response) — DRF's
    APIView.finalize_response accepts any HttpResponseBase, not only
    Response, and apps.payments.views.MockCheckoutView/PayriffReturnView
    already establish this exact idiom for a public, non-JSON browser page
    inside an otherwise-DRF app.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [DependentConsentRateThrottle]

    def get(self, request, pk):
        lang = _page_lang(request)
        token = request.GET.get('token', '')
        try:
            dependent = Dependent.objects.select_related('managed_by').get(pk=pk)
        except Dependent.DoesNotExist:
            return _invalid_page(lang)

        if not consent_token_is_valid(dependent, token):
            return _invalid_page(lang)

        tpl = render_template(
            'dependent_consent_confirm_page', lang,
            account_holder_name=_display_name(dependent.managed_by),
        )
        form_html = f'''<form method="post">
  <input type="hidden" name="token" value="{escape(token)}">
  <input type="hidden" name="lang" value="{escape(lang)}">
  <button type="submit">{escape(tpl['button'])}</button>
</form>'''
        return HttpResponse(_render_page(tpl['heading'], tpl['body'], note=tpl['note'], form_html=form_html))

    def post(self, request, pk):
        lang = _page_lang(request)
        token = request.POST.get('token', '')

        with transaction.atomic():
            # select_for_update + re-validate under the row lock, same
            # TOCTOU-safe pattern as apps.users.views.
            # PasswordResetConfirmView, so two concurrent submits of the
            # same token can't both succeed.
            try:
                dependent = (
                    Dependent.objects.select_related('managed_by')
                    .select_for_update()
                    .get(pk=pk)
                )
            except Dependent.DoesNotExist:
                return _invalid_page(lang)

            if not consent_token_is_valid(dependent, token):
                return _invalid_page(lang)

            account_holder_name = _display_name(dependent.managed_by)
            apply_consent_objection(dependent)

        tpl = render_template(
            'dependent_consent_done_page', lang, account_holder_name=account_holder_name,
        )
        return HttpResponse(_render_page(tpl['heading'], tpl['body']))
