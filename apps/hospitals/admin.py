from django.contrib import admin
from django.utils import timezone

from .models import Hospital, HospitalDoctorLink
from .services import resolve_merge


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    """Follows the same pattern as apps.users.admin.DoctorProfileAdmin's
    verify_doctors/unverify_doctors: bulk actions that call obj.save() per
    row (never queryset.update()) so post_save signals — here,
    apps.hospitals.signals.notify_hospital_claim_reviewed — actually fire.

    Two independent action pairs for Hospital's two status axes (see the
    model's docstring): confirm/reject_registry_entries curate the
    directory-entry-quality axis (a doctor's "add your variant" is legit or
    junk); approve/reject_claims curate the account-claim axis (whoever
    registered actually runs this hospital). A merge is done by hand on the
    change form — set `merged_into` and `status=MERGED`, see save_model.
    """

    list_display = ['name', 'city', 'status', 'claim_status', 'owner', 'created_at']
    list_filter = ['status', 'claim_status', 'city']
    search_fields = ['name', 'owner__email', 'address']
    readonly_fields = [
        'id', 'normalized_name', 'region', 'created_at', 'updated_at',
        'claim_requested_at', 'claim_reviewed_at',
    ]
    raw_id_fields = ['owner', 'created_by', 'merged_into']
    actions = [
        'confirm_registry_entries', 'reject_registry_entries',
        'approve_claims', 'reject_claims',
    ]

    def save_model(self, request, obj, form, change):
        was_merged = change and Hospital.objects.filter(pk=obj.pk, status=Hospital.STATUS_MERGED).exists()
        super().save_model(request, obj, form, change)
        if obj.status == Hospital.STATUS_MERGED and obj.merged_into_id and not was_merged:
            resolve_merge(obj)

    @admin.action(description='Confirm selected registry entries')
    def confirm_registry_entries(self, request, queryset):
        count = 0
        for hospital in queryset.exclude(status=Hospital.STATUS_CONFIRMED):
            hospital.status = Hospital.STATUS_CONFIRMED
            hospital.save(update_fields=['status', 'updated_at'])
            count += 1
        self.message_user(request, f'{count} hospital(s) confirmed.')

    @admin.action(description='Reject selected registry entries')
    def reject_registry_entries(self, request, queryset):
        count = 0
        for hospital in queryset.exclude(status=Hospital.STATUS_REJECTED):
            hospital.status = Hospital.STATUS_REJECTED
            hospital.save(update_fields=['status', 'updated_at'])
            count += 1
        self.message_user(request, f'{count} hospital(s) rejected.')

    @admin.action(description='Approve selected account claims')
    def approve_claims(self, request, queryset):
        count = 0
        now = timezone.now()
        for hospital in queryset.filter(claim_status=Hospital.CLAIM_PENDING):
            hospital.claim_status = Hospital.CLAIM_APPROVED
            hospital.claim_reviewed_at = now
            update_fields = ['claim_status', 'claim_reviewed_at', 'updated_at']
            # Approving the account behind a still-unvetted registry entry
            # (the common case — a hospital self-registering a brand-new
            # listing, see apps.hospitals.services.claim_or_create_hospital)
            # implicitly vets the listing too: an admin-approved hospital
            # account obviously isn't a bogus directory entry.
            if hospital.status == Hospital.STATUS_PENDING_REVIEW:
                hospital.status = Hospital.STATUS_CONFIRMED
                update_fields.append('status')
            hospital.save(update_fields=update_fields)
            count += 1
        self.message_user(request, f'{count} claim(s) approved.')

    @admin.action(description='Reject selected account claims')
    def reject_claims(self, request, queryset):
        count = 0
        now = timezone.now()
        for hospital in queryset.filter(claim_status=Hospital.CLAIM_PENDING):
            hospital.claim_status = Hospital.CLAIM_REJECTED
            hospital.claim_reviewed_at = now
            hospital.save(update_fields=['claim_status', 'claim_reviewed_at', 'updated_at'])
            count += 1
        self.message_user(request, f'{count} claim(s) rejected.')


@admin.register(HospitalDoctorLink)
class HospitalDoctorLinkAdmin(admin.ModelAdmin):
    """Read-only visibility for support/debugging — the actual state
    transitions always go through apps.hospitals.services (approve_link/
    reject_link/remove_doctor/invite_doctor), never edited by hand here,
    since those functions also null out Workplace.hospital in the same
    transaction (see services.py)."""

    list_display = ['hospital', 'doctor', 'status', 'requested_by', 'updated_at']
    list_filter = ['status', 'requested_by']
    search_fields = ['hospital__name', 'doctor__email']
    raw_id_fields = ['hospital', 'doctor', 'decided_by']
    readonly_fields = ['id', 'created_at', 'updated_at', 'decided_at']
