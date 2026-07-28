from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.hospitals.models import Hospital

from .models import DoctorProfile, PatientProfile, SocialAccount, User, UserDevice


class DoctorProfileInline(admin.StackedInline):
    model = DoctorProfile
    can_delete = False


class PatientProfileInline(admin.StackedInline):
    model = PatientProfile
    can_delete = False


class HospitalInline(admin.StackedInline):
    """Read-only convenience view of the Hospital row this account owns —
    the actual claim-approval and registry-curation actions live on
    apps.hospitals.admin.HospitalAdmin (registered separately, same
    relationship as DoctorProfileAdmin's verify/unverify actions above),
    not here."""

    model = Hospital
    fk_name = 'owner'
    can_delete = False
    fields = ['id', 'name', 'city', 'status', 'claim_status']
    readonly_fields = fields


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'role', 'is_active', 'is_staff', 'created_at']
    list_filter = ['role', 'is_active', 'is_staff']
    search_fields = ['email', 'first_name', 'last_name']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']

    fieldsets = (
        (None, {'fields': ('id', 'email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'role')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at', 'last_login')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'role', 'first_name', 'last_name', 'password1', 'password2'),
        }),
    )

    def get_inlines(self, request, obj=None):
        if obj is None:
            return []
        if obj.role == User.ROLE_DOCTOR:
            return [DoctorProfileInline]
        if obj.role == User.ROLE_PATIENT:
            return [PatientProfileInline]
        if obj.role == User.ROLE_HOSPITAL:
            return [HospitalInline]
        return []


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'specialization', 'is_verified', 'onboarding_complete', 'onboarding_step']
    list_filter = ['is_verified', 'onboarding_complete']
    search_fields = ['user__email', 'specialization', 'license_number']
    readonly_fields = ['user']
    actions = ['verify_doctors', 'unverify_doctors']

    @admin.action(description='Verify selected doctors')
    def verify_doctors(self, request, queryset):
        count = 0
        for profile in queryset.filter(is_verified=False):
            profile.is_verified = True
            profile.save(update_fields=['is_verified'])
            count += 1
        self.message_user(request, f'{count} doctor(s) verified.')

    @admin.action(description='Unverify selected doctors')
    def unverify_doctors(self, request, queryset):
        count = 0
        for profile in queryset.filter(is_verified=True):
            profile.is_verified = False
            profile.save(update_fields=['is_verified'])
            count += 1
        self.message_user(request, f'{count} doctor(s) unverified.')


@admin.register(PatientProfile)
class PatientProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'blood_type', 'date_of_birth']
    search_fields = ['user__email']
    readonly_fields = ['user']


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ['user', 'provider', 'email', 'created_at']
    list_filter = ['provider']
    search_fields = ['user__email', 'email', 'provider_uid']
    readonly_fields = ['user', 'provider', 'provider_uid', 'email', 'created_at']


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_name', 'platform', 'last_seen_at', 'created_at']
    list_filter = ['platform']
    search_fields = ['user__email', 'device_name', 'device_id']
    readonly_fields = ['user', 'device_id', 'jti', 'created_at']
