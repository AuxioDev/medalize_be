import datetime
import string

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.appointments.models import Appointment
from apps.doctors.models import Workplace
from apps.notifications.i18n import _TEMPLATES, recipient_language, render_template
from apps.notifications.models import Notification
from apps.notifications.tasks import send_booking_cancelled, send_booking_confirmed
from apps.users.i18n import _SPECIALIZATIONS, specialization_label, viewer_language

User = get_user_model()

ME_URL = '/api/auth/me/'
DOCTOR_PROFILE_URL = '/api/doctor/profile/'
DOCTORS_URL = '/api/doctors/'
PASSWORD_RESET_URL = '/api/auth/password/reset/'

LANGS = ['en', 'ru', 'az', 'tr', 'fr', 'zh']


class TemplateRegistryTests(SimpleTestCase):
    def test_render_template_english(self):
        tpl = render_template('booking_confirmed', 'en', doctor_name='John Smith',
                              date_str='05.07.2026 14:30')
        self.assertEqual(tpl['title'], 'Appointment Confirmed')
        self.assertEqual(
            tpl['body'],
            'Your appointment with Dr. John Smith on 05.07.2026 14:30 has been confirmed.',
        )

    def test_render_template_russian(self):
        tpl = render_template('booking_confirmed', 'ru', doctor_name='John Smith',
                              date_str='05.07.2026 14:30')
        self.assertEqual(tpl['title'], 'Приём подтверждён')
        self.assertIn('John Smith', tpl['body'])

    def test_render_template_unknown_language_falls_back_to_english(self):
        tpl = render_template('booking_confirmed', 'de', doctor_name='X', date_str='Y')
        self.assertEqual(tpl['title'], 'Appointment Confirmed')

    def test_render_template_blank_language_falls_back_to_english(self):
        tpl = render_template('doctor_verified', '')
        self.assertEqual(tpl['title'], 'Account Verified')

    def test_render_template_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            render_template('no_such_template', 'en')

    def test_all_templates_cover_all_languages_with_matching_placeholders(self):
        formatter = string.Formatter()

        def placeholders(text):
            return {name for _, name, _, _ in formatter.parse(text) if name}

        for key, variants in _TEMPLATES.items():
            en = variants['en']
            for lang in LANGS:
                self.assertIn(lang, variants, f'{key} is missing language {lang}')
                self.assertEqual(
                    set(variants[lang].keys()), set(en.keys()),
                    f'{key}/{lang} has different fields than en',
                )
                for field, text in variants[lang].items():
                    self.assertEqual(
                        placeholders(text), placeholders(en[field]),
                        f'{key}/{lang}/{field} placeholders differ from en',
                    )

    def test_specialization_label_localized(self):
        self.assertEqual(specialization_label('cardiology', 'en'), 'Cardiology')
        self.assertEqual(specialization_label('cardiology', 'ru'), 'Кардиология')

    def test_specialization_label_unknown_language_falls_back_to_english(self):
        self.assertEqual(specialization_label('cardiology', 'de'), 'Cardiology')
        self.assertEqual(specialization_label('cardiology', ''), 'Cardiology')

    def test_specialization_label_unknown_code_returns_code(self):
        self.assertEqual(specialization_label('astrology', 'ru'), 'astrology')

    def test_all_specializations_cover_all_languages(self):
        from apps.users.models import DoctorProfile
        model_codes = {code for code, _ in DoctorProfile.SPECIALIZATION_CHOICES}
        self.assertEqual(set(_SPECIALIZATIONS.keys()), model_codes)
        for code, entry in _SPECIALIZATIONS.items():
            for lang in LANGS:
                self.assertIn(lang, entry, f'{code} is missing language {lang}')
                self.assertTrue(entry[lang], f'{code}/{lang} is blank')

    def test_language_helpers_defensive_defaults(self):
        self.assertEqual(recipient_language(None), 'en')
        self.assertEqual(recipient_language(object()), 'en')
        self.assertEqual(viewer_language({}), 'en')
        self.assertEqual(viewer_language(None), 'en')


class UserLanguageFieldTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email='patient@test.com', password='Pass1234', role='patient',
            first_name='Jane', last_name='Doe',
        )
        self.client.force_authenticate(self.user)

    def test_new_user_defaults_to_english(self):
        self.assertEqual(self.user.language, 'en')
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['language'], 'en')

    def test_patch_me_changes_language(self):
        res = self.client.patch(ME_URL, {'language': 'ru'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['language'], 'ru')
        self.user.refresh_from_db()
        self.assertEqual(self.user.language, 'ru')

    def test_patch_me_rejects_unsupported_language(self):
        res = self.client.patch(ME_URL, {'language': 'xx'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertEqual(self.user.language, 'en')


class LocalizedSpecializationDisplayTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.doctor = User.objects.create_user(
            email='doctor@test.com', password='Pass1234', role='doctor',
            first_name='John', last_name='Smith', language='tr',
        )
        profile = self.doctor.doctor_profile
        profile.specialization = 'cardiology'
        profile.is_verified = True
        profile.save(update_fields=['specialization', 'is_verified'])

        self.patient = User.objects.create_user(
            email='patient@test.com', password='Pass1234', role='patient',
            first_name='Jane', last_name='Doe', language='ru',
        )

    def test_doctor_list_uses_viewing_patients_language(self):
        self.client.force_authenticate(self.patient)
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['results'][0]['specialization_display'], 'Кардиология')
        # The raw code stays untranslated for client-side logic.
        self.assertEqual(res.data['results'][0]['specialization'], 'cardiology')

    def test_doctor_detail_uses_viewing_patients_language(self):
        self.client.force_authenticate(self.patient)
        res = self.client.get(f'{DOCTORS_URL}{self.doctor.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['specialization_display'], 'Кардиология')

    def test_doctor_list_english_for_english_viewer(self):
        self.patient.language = 'en'
        self.patient.save(update_fields=['language'])
        self.client.force_authenticate(self.patient)
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.data['results'][0]['specialization_display'], 'Cardiology')

    def test_own_doctor_profile_uses_doctors_language(self):
        self.client.force_authenticate(self.doctor)
        res = self.client.get(DOCTOR_PROFILE_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['specialization_display'], 'Kardiyoloji')

    def test_me_endpoint_profile_uses_doctors_language(self):
        self.client.force_authenticate(self.doctor)
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['profile']['specialization_display'], 'Kardiyoloji')

    def test_appointment_serializer_uses_viewing_patients_language(self):
        workplace = Workplace.objects.create(
            doctor=self.doctor, name='Clinic', address='1 St', city='Baku', type='clinic',
        )
        starts = timezone.now() + datetime.timedelta(days=3)
        appt = Appointment.objects.create(
            doctor=self.doctor, patient=self.patient, workplace=workplace,
            starts_at=starts, ends_at=starts + datetime.timedelta(minutes=30),
        )
        self.client.force_authenticate(self.patient)
        res = self.client.get(f'/api/appointments/{appt.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['doctor']['specialization_display'], 'Кардиология')


class LocalizedNotificationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.doctor = User.objects.create_user(
            email='doctor@test.com', password='Pass1234', role='doctor',
            first_name='John', last_name='Smith', language='en',
        )
        self.patient = User.objects.create_user(
            email='patient@test.com', password='Pass1234', role='patient',
            first_name='Jane', last_name='Doe', language='ru',
        )
        self.workplace = Workplace.objects.create(
            doctor=self.doctor, name='Clinic', address='1 St', city='Baku', type='clinic',
        )
        starts = timezone.now() + datetime.timedelta(days=3)
        self.appointment = Appointment.objects.create(
            doctor=self.doctor, patient=self.patient, workplace=self.workplace,
            starts_at=starts, ends_at=starts + datetime.timedelta(minutes=30),
        )
        mail.outbox = []

    def test_booking_confirmed_rendered_in_patients_language(self):
        send_booking_confirmed(str(self.appointment.id))
        notif = Notification.objects.get(user=self.patient)
        self.assertEqual(notif.title, 'Приём подтверждён')
        self.assertIn('John Smith', notif.message)
        self.assertNotIn('Dr. John Smith', notif.message)  # ru text has no 'Dr.' prefix
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Приём подтверждён — Medalize')
        # Numeric date format, no English month name.
        date_str = self.appointment.starts_at.strftime('%d.%m.%Y %H:%M')
        self.assertIn(date_str, notif.message)

    def test_booking_cancelled_each_party_gets_their_own_language(self):
        send_booking_cancelled(str(self.appointment.id))
        patient_notif = Notification.objects.get(user=self.patient)
        doctor_notif = Notification.objects.get(user=self.doctor)
        self.assertEqual(patient_notif.title, 'Приём отменён')
        self.assertEqual(doctor_notif.title, 'Appointment Cancelled')
        self.assertIn('Jane Doe', doctor_notif.message)

    def test_booking_confirmed_english_default_matches_legacy_text(self):
        self.patient.language = 'en'
        self.patient.save(update_fields=['language'])
        send_booking_confirmed(str(self.appointment.id))
        notif = Notification.objects.get(user=self.patient)
        date_str = self.appointment.starts_at.strftime('%d.%m.%Y %H:%M')
        self.assertEqual(
            notif.message,
            f'Your appointment with Dr. John Smith on {date_str} has been confirmed.',
        )
        self.assertEqual(mail.outbox[0].subject, 'Appointment Confirmed — Medalize')

    def test_password_reset_email_in_users_language(self):
        res = self.client.post(PASSWORD_RESET_URL, {'email': 'patient@test.com'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Код сброса пароля — Medalize')
        self.assertIn('Ваш код для сброса пароля', mail.outbox[0].body)
