"""Subscription tier definitions for both billable account types (doctors
and hospitals).

Plain module constants rather than a database table — same style as
apps.users.i18n.specializations.json's SPECIALIZATION_CHOICES: there are
exactly four paid tiers total, changing a price is a deploy, and a
DB-editable plan table would be a foot-gun (an admin fat-fingering a price
directly changes what Payriff charges next checkout).
"""
from decimal import Decimal

PLAN_BASIC = 'basic'
PLAN_PRO = 'pro'
PLAN_HOSPITAL_BASIC = 'hospital_basic'
PLAN_HOSPITAL_PRO = 'hospital_pro'

PLAN_PRICES = {
    PLAN_BASIC: Decimal('19.99'),
    PLAN_PRO: Decimal('39.99'),
    PLAN_HOSPITAL_BASIC: Decimal('299.99'),
    PLAN_HOSPITAL_PRO: Decimal('599.99'),
}

# Displayed to the account holder as the plan name. Not run through the i18n
# template registry — these are brand names for the tiers (Azerbaijani,
# matching the target market), not translated strings.
PLAN_NAMES = {
    PLAN_BASIC: 'Başlanğıc',
    PLAN_PRO: 'Peşəkar',
    PLAN_HOSPITAL_BASIC: 'Klinika',
    PLAN_HOSPITAL_PRO: 'Klinika Plus',
}

PERIOD_DAYS = 30
TRIAL_DAYS = 7
GRACE_DAYS = 14

# Plain role strings ('doctor'/'hospital'), not apps.users.models.User.ROLE_*
# — this module must stay import-free of apps.users to avoid a circular
# import (apps.users.serializers already imports from apps.subscriptions).
ROLE_DOCTOR = 'doctor'
ROLE_HOSPITAL = 'hospital'

PLANS_FOR_ROLE = {
    ROLE_DOCTOR: (PLAN_BASIC, PLAN_PRO),
    ROLE_HOSPITAL: (PLAN_HOSPITAL_BASIC, PLAN_HOSPITAL_PRO),
}

# Roles that get a trial at verification/approval time. Hospitals are
# deliberately excluded — approval goes straight to the paywall, no trial.
TRIAL_ROLES = (ROLE_DOCTOR,)

# Keyed by apps.subscriptions.entitlements.effective_plan_key(), not by
# Subscription.status directly — 'trial' and 'none' aren't plan codes stored
# on the model, they're synthetic keys for "currently trialing" and "no
# entitlement at all" (expired/pending).
PLAN_LIMITS = {
    'trial': {
        'workplaces': 5,
        'appointments_per_month': None,
        'chat': True,
        'promoted': True,
        'advanced_stats': True,
    },
    PLAN_BASIC: {
        'workplaces': 1,
        'appointments_per_month': 40,
        'chat': False,
        'promoted': False,
        'advanced_stats': False,
    },
    PLAN_PRO: {
        'workplaces': 5,
        'appointments_per_month': None,
        'chat': True,
        'promoted': True,
        'advanced_stats': True,
    },
    'none': {
        'workplaces': 0,
        'appointments_per_month': 0,
        'chat': False,
        'promoted': False,
        'advanced_stats': False,
    },
}

# Hospital plans have a genuinely different key set (doctors, not
# workplaces/appointments/chat/promoted) — kept as a separate table rather
# than merged into PLAN_LIMITS so a lookup under the wrong role's key set
# raises a loud KeyError instead of silently returning None.
#
# Seat pricing: hospital_basic (299.99) / 15 doctors ≈ 20 AZN per doctor,
# matching the doctor's own Başlanğıc price — the tier reads as "bulk seats
# at the same per-seat rate". hospital_pro doubles the price for 4x the
# seats plus advanced stats, mirroring how the doctor Pro tier removes caps
# for double the Basic price.
HOSPITAL_PLAN_LIMITS = {
    PLAN_HOSPITAL_BASIC: {
        'doctors': 15,
        'advanced_stats': False,
    },
    PLAN_HOSPITAL_PRO: {
        'doctors': 60,
        'advanced_stats': True,
    },
    'none': {
        'doctors': 0,
        'advanced_stats': False,
    },
}

LIMITS_BY_ROLE = {
    ROLE_DOCTOR: PLAN_LIMITS,
    ROLE_HOSPITAL: HOSPITAL_PLAN_LIMITS,
}
