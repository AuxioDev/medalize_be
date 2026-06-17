from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import DoctorProfile, PatientProfile, User


class DoctorProfileInline(admin.StackedInline):
    model = DoctorProfile
    can_delete = False


class PatientProfileInline(admin.StackedInline):
    model = PatientProfile
    can_delete = False


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
        return []


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'specialization', 'is_verified', 'onboarding_complete', 'onboarding_step']
    list_filter = ['is_verified', 'onboarding_complete']
    search_fields = ['user__email', 'specialization', 'license_number']
    readonly_fields = ['user']


@admin.register(PatientProfile)
class PatientProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'blood_type', 'date_of_birth']
    search_fields = ['user__email']
    readonly_fields = ['user']
