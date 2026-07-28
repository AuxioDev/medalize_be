"""Doctor subscription tier definitions.

Plain module constants rather than a database table — same style as
apps.users.i18n.specializations.json's SPECIALIZATION_CHOICES: there are
exactly two paid tiers, changing a price is a deploy, and a DB-editable plan
table would be a foot-gun (an admin fat-fingering a price directly changes
what Payriff charges next checkout).
"""
from decimal import Decimal

PLAN_BASIC = 'basic'
PLAN_PRO = 'pro'

PLAN_PRICES = {
    PLAN_BASIC: Decimal('19.99'),
    PLAN_PRO: Decimal('39.99'),
}

# Displayed to doctors as the plan name. Not run through the i18n template
# registry — these are brand names for the tiers (Azerbaijani, matching the
# target market), not translated strings.
PLAN_NAMES = {
    PLAN_BASIC: 'Başlanğıc',
    PLAN_PRO: 'Peşəkar',
}

PERIOD_DAYS = 30
TRIAL_DAYS = 7
GRACE_DAYS = 14

# Keyed by apps.subscriptions.entitlements.effective_plan_key(), not by
# DoctorSubscription.status directly — 'trial' and 'none' aren't plan codes
# stored on the model, they're synthetic keys for "currently trialing" and
# "no entitlement at all" (expired/pending).
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
