from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.doctors.models import BlockedPeriod, WorkingHours, Workplace
from apps.notifications.models import Notification
from apps.users.models import DoctorProfile, PatientProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Seed the database with Azerbaijan-specific mock data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing non-superuser data before seeding',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing data...')
            Notification.objects.all().delete()
            Appointment.objects.all().delete()
            BlockedPeriod.objects.all().delete()
            WorkingHours.objects.all().delete()
            Workplace.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.SUCCESS('Cleared.'))

        self.stdout.write('Seeding doctors...')
        doctors = self._seed_doctors()
        self.stdout.write(self.style.SUCCESS(f'  {len(doctors)} doctors ready.'))

        self.stdout.write('Seeding patients...')
        patients = self._seed_patients()
        self.stdout.write(self.style.SUCCESS(f'  {len(patients)} patients ready.'))

        self.stdout.write('Seeding workplaces & working hours...')
        workplaces = self._seed_workplaces(doctors)
        self.stdout.write(self.style.SUCCESS(f'  {len(workplaces)} workplaces ready.'))

        self.stdout.write('Seeding blocked periods...')
        self._seed_blocked_periods(doctors, workplaces)
        self.stdout.write(self.style.SUCCESS('  Blocked periods ready.'))

        self.stdout.write('Seeding appointments...')
        appointments = self._seed_appointments(doctors, patients, workplaces)
        self.stdout.write(self.style.SUCCESS(f'  {len(appointments)} appointments ready.'))

        self.stdout.write('Seeding notifications...')
        count = self._seed_notifications(appointments)
        self.stdout.write(self.style.SUCCESS(f'  {count} notifications ready.'))

        self.stdout.write(self.style.SUCCESS('\nSeeding complete.'))

    # ─────────────────────────────────────────────────────────────────────────
    # Doctors
    # ─────────────────────────────────────────────────────────────────────────

    def _seed_doctors(self):
        doctor_data = [
            {
                'email': 'ali.hasanov@medalize.az',
                'first_name': 'Əli',
                'last_name': 'Həsənov',
                'phone': '+994501234567',
                'specialization': 'cardiology',
                'license_number': 'AZ-MED-2015-0142',
                'bio': (
                    'Kardioloq kimi 10 illik təcrübəyə sahibəm. '
                    'Ürək-damar xəstəliklərinin diaqnostika və müalicəsi üzrə ixtisaslaşmışam.'
                ),
            },
            {
                'email': 'nigar.quliyeva@medalize.az',
                'first_name': 'Nigar',
                'last_name': 'Quliyeva',
                'phone': '+994702345678',
                'specialization': 'pediatrics',
                'license_number': 'AZ-MED-2018-0387',
                'bio': (
                    'Uşaq həkimi olaraq 7 il ərzində minlərlə körpə və uşağa '
                    'sağlamlıq xidməti göstərmişəm.'
                ),
            },
            {
                'email': 'tural.ismayilov@medalize.az',
                'first_name': 'Tural',
                'last_name': 'İsmayılov',
                'phone': '+994993456789',
                'specialization': 'general_practice',
                'license_number': 'AZ-MED-2012-0089',
                'bio': (
                    'Ümumi praktik həkim olaraq Sumqayıt şəhərində 12 ildir '
                    'fəaliyyət göstərirəm.'
                ),
            },
            {
                'email': 'leyla.babayeva@medalize.az',
                'first_name': 'Leyla',
                'last_name': 'Babayeva',
                'phone': '+994554567890',
                'specialization': 'dermatology',
                'license_number': 'AZ-MED-2019-0521',
                'bio': (
                    'Dərmatologiya sahəsindəki müasir müalicə metodları ilə '
                    'xəstələrimə ən yaxşı nəticəni təmin edirəm.'
                ),
            },
            {
                'email': 'orxan.aliyev@medalize.az',
                'first_name': 'Orxan',
                'last_name': 'Əliyev',
                'phone': '+994775678901',
                'specialization': 'neurology',
                'license_number': 'AZ-MED-2014-0263',
                'bio': (
                    'Nevroloq olaraq baş ağrısı, epilepsiya və digər sinir sistemi '
                    'xəstəlikləri üzrə ixtisaslaşmışam.'
                ),
            },
        ]

        doctors = []
        for data in doctor_data:
            profile_fields = {
                'specialization': data.pop('specialization'),
                'license_number': data.pop('license_number'),
                'bio': data.pop('bio'),
            }
            user, created = User.objects.get_or_create(
                email=data['email'],
                defaults={**data, 'role': User.ROLE_DOCTOR},
            )
            if created:
                user.set_password('Doctor@1234')
                user.save()

            # Profile is auto-created by post_save signal; update its fields.
            profile = user.doctor_profile
            profile.specialization = profile_fields['specialization']
            profile.license_number = profile_fields['license_number']
            profile.bio = profile_fields['bio']
            profile.is_verified = True
            profile.onboarding_complete = True
            profile.onboarding_step = 4
            profile.slot_duration_min = 30
            profile.save()

            doctors.append(user)
        return doctors

    # ─────────────────────────────────────────────────────────────────────────
    # Patients
    # ─────────────────────────────────────────────────────────────────────────

    def _seed_patients(self):
        patient_data = [
            {
                'email': 'aysel.huseynova@gmail.com',
                'first_name': 'Aysel',
                'last_name': 'Hüseynova',
                'phone': '+994506781234',
                'date_of_birth': date(1990, 3, 15),
                'blood_type': 'A+',
                'address': 'Bakı, Nəsimi r., Füzuli küçəsi 12',
            },
            {
                'email': 'rauf.qahramanov@gmail.com',
                'first_name': 'Rauf',
                'last_name': 'Qəhrəmanov',
                'phone': '+994707892345',
                'date_of_birth': date(1985, 7, 22),
                'blood_type': 'B+',
                'address': 'Gəncə, Nizami küçəsi 45',
            },
            {
                'email': 'gunel.nasirzade@gmail.com',
                'first_name': 'Günel',
                'last_name': 'Nəsirzadə',
                'phone': '+994998903456',
                'date_of_birth': date(1995, 11, 8),
                'blood_type': 'O+',
                'address': 'Sumqayıt, 27-ci mikrorayon',
            },
            {
                'email': 'elcin.valiyev@gmail.com',
                'first_name': 'Elçin',
                'last_name': 'Vəliyev',
                'phone': '+994559014567',
                'date_of_birth': date(1988, 4, 30),
                'blood_type': 'AB+',
                'address': 'Bakı, Xətai r., Akad. H.Əliyev küçəsi 7',
            },
            {
                'email': 'sevinc.mammadova@gmail.com',
                'first_name': 'Sevinc',
                'last_name': 'Məmmədova',
                'phone': '+994779125678',
                'date_of_birth': date(1992, 9, 17),
                'blood_type': 'A-',
                'address': 'Lənkəran, Hüseyn Cavid küçəsi 23',
            },
            {
                'email': 'murad.mirzayev@gmail.com',
                'first_name': 'Murad',
                'last_name': 'Mirzəyev',
                'phone': '+994519236789',
                'date_of_birth': date(1983, 1, 5),
                'blood_type': 'B-',
                'address': 'Mingəçevir, Zəfər küçəsi 11',
            },
            {
                'email': 'xadica.rahimova@gmail.com',
                'first_name': 'Xədicə',
                'last_name': 'Rəhimova',
                'phone': '+994709347890',
                'date_of_birth': date(1998, 6, 28),
                'blood_type': 'O-',
                'address': 'Bakı, Sabunçu r., Maştağa qəs., Azərbaycan küçəsi 34',
            },
            {
                'email': 'kamran.abbasov@gmail.com',
                'first_name': 'Kamran',
                'last_name': 'Abbasov',
                'phone': '+994998458901',
                'date_of_birth': date(1979, 12, 14),
                'blood_type': 'A+',
                'address': 'Şirvan, Müstəqillik küçəsi 18',
            },
            {
                'email': 'lale.asgarova@gmail.com',
                'first_name': 'Lalə',
                'last_name': 'Əsgərova',
                'phone': '+994559569012',
                'date_of_birth': date(2000, 2, 11),
                'blood_type': 'AB-',
                'address': 'Bakı, Binəqədi r., 7-ci massiv, M.Müşfiq küçəsi 5',
            },
            {
                'email': 'farah.sukurlu@gmail.com',
                'first_name': 'Fərəh',
                'last_name': 'Şükürlü',
                'phone': '+994776670123',
                'date_of_birth': date(1993, 8, 19),
                'blood_type': 'B+',
                'address': 'Gəncə, Mustafayev küçəsi 67',
            },
        ]

        patients = []
        for data in patient_data:
            profile_fields = {
                'date_of_birth': data.pop('date_of_birth'),
                'blood_type': data.pop('blood_type'),
                'address': data.pop('address'),
            }
            user, created = User.objects.get_or_create(
                email=data['email'],
                defaults={**data, 'role': User.ROLE_PATIENT},
            )
            if created:
                user.set_password('Patient@1234')
                user.save()

            profile = user.patient_profile
            profile.date_of_birth = profile_fields['date_of_birth']
            profile.blood_type = profile_fields['blood_type']
            profile.address = profile_fields['address']
            profile.save()

            patients.append(user)
        return patients

    # ─────────────────────────────────────────────────────────────────────────
    # Workplaces & WorkingHours
    # ─────────────────────────────────────────────────────────────────────────

    def _seed_workplaces(self, doctors):
        workplace_data = [
            # Dr. Əli Həsənov — Cardiology
            {
                'doctor_idx': 0,
                'name': 'Respublika Kardioloji Mərkəzi',
                'address': 'Bakı şəhəri, Lermontov küçəsi 122',
                'city': 'Bakı',
                'type': 'hospital',
                'is_primary': True,
                'active_days': [0, 1, 2, 3, 4],
                'start': time(9, 0),
                'end': time(17, 0),
            },
            {
                'doctor_idx': 0,
                'name': 'MedLife Klinikası',
                'address': 'Bakı şəhəri, İstiqlaliyyət küçəsi 33',
                'city': 'Bakı',
                'type': 'clinic',
                'is_primary': False,
                'active_days': [5],
                'start': time(10, 0),
                'end': time(15, 0),
            },
            # Dr. Nigar Quliyeva — Pediatrics
            {
                'doctor_idx': 1,
                'name': 'Uşaq Klinik Xəstəxanası №1',
                'address': 'Bakı şəhəri, Hüsü Hacıyev küçəsi 67',
                'city': 'Bakı',
                'type': 'hospital',
                'is_primary': True,
                'active_days': [0, 1, 2, 3, 4],
                'start': time(8, 0),
                'end': time(16, 0),
            },
            # Dr. Tural İsmayılov — General Practice
            {
                'doctor_idx': 2,
                'name': 'Sumqayıt Şəhər Poliklinikası №3',
                'address': 'Sumqayıt şəhəri, 14-cü mikrorayon',
                'city': 'Sumqayıt',
                'type': 'clinic',
                'is_primary': True,
                'active_days': [0, 1, 2, 3, 4],
                'start': time(9, 0),
                'end': time(18, 0),
            },
            {
                'doctor_idx': 2,
                'name': 'Özel Sağlıq Mərkəzi',
                'address': 'Sumqayıt şəhəri, Azneft meydanı 2',
                'city': 'Sumqayıt',
                'type': 'private',
                'is_primary': False,
                'active_days': [5, 6],
                'start': time(10, 0),
                'end': time(14, 0),
            },
            # Dr. Leyla Babayeva — Dermatology
            {
                'doctor_idx': 3,
                'name': 'Dərmatologiya və Kosmetologiya Mərkəzi',
                'address': 'Bakı şəhəri, Nizami küçəsi 56',
                'city': 'Bakı',
                'type': 'clinic',
                'is_primary': True,
                'active_days': [0, 1, 2, 3, 4, 5],
                'start': time(10, 0),
                'end': time(19, 0),
            },
            # Dr. Orxan Əliyev — Neurology
            {
                'doctor_idx': 4,
                'name': 'Gəncə Regional Xəstəxanası',
                'address': 'Gəncə şəhəri, Mustafayev küçəsi 134',
                'city': 'Gəncə',
                'type': 'hospital',
                'is_primary': True,
                'active_days': [0, 1, 2, 3, 4],
                'start': time(8, 30),
                'end': time(16, 30),
            },
        ]

        workplaces = []
        for data in workplace_data:
            doctor = doctors[data.pop('doctor_idx')]
            active_days = data.pop('active_days')
            start = data.pop('start')
            end = data.pop('end')

            wp, created = Workplace.objects.get_or_create(
                doctor=doctor,
                name=data['name'],
                defaults={**data, 'doctor': doctor},
            )
            if created:
                for weekday in range(7):
                    WorkingHours.objects.create(
                        workplace=wp,
                        weekday=weekday,
                        start_time=start,
                        end_time=end,
                        is_active=(weekday in active_days),
                    )
            workplaces.append(wp)
        return workplaces

    # ─────────────────────────────────────────────────────────────────────────
    # Blocked Periods
    # ─────────────────────────────────────────────────────────────────────────

    def _seed_blocked_periods(self, doctors, workplaces):
        now = timezone.now()
        blocked_data = [
            {
                'doctor': doctors[4],  # Orxan Əliyev
                'workplace': next((w for w in workplaces if w.doctor == doctors[4]), None),
                'starts_at': now + timedelta(days=1, hours=0),
                'ends_at': now + timedelta(days=1, hours=8),
                'reason': 'Elmi konfrans — Azərbaycan Nevrologiya Cəmiyyəti iclası',
            },
            {
                'doctor': doctors[0],  # Əli Həsənov
                'workplace': None,
                'starts_at': now + timedelta(days=15),
                'ends_at': now + timedelta(days=17),
                'reason': 'Məzuniyyət',
            },
        ]

        for data in blocked_data:
            BlockedPeriod.objects.get_or_create(
                doctor=data['doctor'],
                starts_at=data['starts_at'],
                defaults=data,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Appointments
    # ─────────────────────────────────────────────────────────────────────────

    def _seed_appointments(self, doctors, patients, workplaces):
        # Index primary workplaces by doctor id
        primary_wp = {
            wp.doctor_id: wp
            for wp in workplaces
            if wp.is_primary
        }

        now = timezone.now().replace(minute=0, second=0, microsecond=0)

        raw = [
            # ── Past / completed ────────────────────────────────────────────
            {
                'doctor': doctors[0], 'patient': patients[0],
                'delta_days': -14, 'hour': 10,
                'status': Appointment.STATUS_COMPLETED,
                'reason': 'Ürək döyüntüsündə narahatlıq və nəfəs darlığı şikayəti',
                'notes': 'EKQ normal. AT: 130/80. Növbəti müayinə 3 aydan sonra.',
            },
            {
                'doctor': doctors[1], 'patient': patients[2],
                'delta_days': -7, 'hour': 14,
                'status': Appointment.STATUS_COMPLETED,
                'reason': 'Uşaqda temperatur və öskürək',
                'notes': 'ARVI diaqnozu. Müalicə təyin olundu.',
            },
            {
                'doctor': doctors[2], 'patient': patients[5],
                'delta_days': -3, 'hour': 9,
                'status': Appointment.STATUS_COMPLETED,
                'reason': 'Baş ağrısı və ümumi zəiflik',
                'notes': 'Qan təzyiqi yüksəkdir. Antihipertensiv müalicə başlandı.',
            },
            {
                'doctor': doctors[3], 'patient': patients[8],
                'delta_days': -5, 'hour': 16,
                'status': Appointment.STATUS_CANCELLED,
                'reason': 'Saç tökülməsi problemi',
                'notes': '',
            },
            # ── Upcoming / confirmed ─────────────────────────────────────────
            {
                'doctor': doctors[0], 'patient': patients[3],
                'delta_days': 2, 'hour': 11,
                'status': Appointment.STATUS_CONFIRMED,
                'reason': 'Döş qəfəsindəki ağrı — kardioloji ilkin qiymətləndirmə',
                'notes': '',
            },
            {
                'doctor': doctors[3], 'patient': patients[4],
                'delta_days': 3, 'hour': 15,
                'status': Appointment.STATUS_CONFIRMED,
                'reason': 'Dəridə qaşınan töküntülər',
                'notes': '',
            },
            {
                'doctor': doctors[1], 'patient': patients[6],
                'delta_days': 5, 'hour': 10,
                'status': Appointment.STATUS_CONFIRMED,
                'reason': 'Körpənin 6 aylıq profilaktik müayinəsi',
                'notes': '',
            },
            # ── Pending ──────────────────────────────────────────────────────
            {
                'doctor': doctors[4], 'patient': patients[1],
                'delta_days': 7, 'hour': 9,
                'status': Appointment.STATUS_PENDING,
                'reason': 'Uzun sürən miqren və baş fırlanması',
                'notes': '',
            },
            {
                'doctor': doctors[2], 'patient': patients[7],
                'delta_days': 4, 'hour': 13,
                'status': Appointment.STATUS_PENDING,
                'reason': 'Boğaz ağrısı və soyuqdəymə simptomları',
                'notes': '',
            },
            {
                'doctor': doctors[0], 'patient': patients[9],
                'delta_days': 10, 'hour': 12,
                'status': Appointment.STATUS_PENDING,
                'reason': 'İdmançı sağlamlıq yoxlaması — kardioloji qiymətləndirmə',
                'notes': '',
            },
            # ── Requires rescheduling ────────────────────────────────────────
            {
                'doctor': doctors[4], 'patient': patients[0],
                'delta_days': 1, 'hour': 14,
                'status': Appointment.STATUS_REQUIRES_RESCHEDULING,
                'reason': 'Yuxu pozğunluqları və baş ağrısı',
                'notes': 'Həkim elmi konfransda iştirak edəcək — yenidən planlaşdırma lazımdır.',
            },
        ]

        appointments = []
        for item in raw:
            doctor = item['doctor']
            wp = primary_wp.get(doctor.id)
            if not wp:
                continue
            starts_at = (now + timedelta(days=item['delta_days'])).replace(
                hour=item['hour'], minute=0, second=0, microsecond=0
            )
            ends_at = starts_at + timedelta(minutes=30)

            appt, created = Appointment.objects.get_or_create(
                doctor=doctor,
                patient=item['patient'],
                starts_at=starts_at,
                defaults={
                    'workplace': wp,
                    'ends_at': ends_at,
                    'status': item['status'],
                    'reason': item['reason'],
                    'notes': item['notes'],
                },
            )
            if created:
                appointments.append(appt)
        return appointments

    # ─────────────────────────────────────────────────────────────────────────
    # Notifications
    # ─────────────────────────────────────────────────────────────────────────

    def _seed_notifications(self, appointments):
        created_count = 0
        for appt in appointments:
            entries = []

            if appt.status == Appointment.STATUS_CONFIRMED:
                entries.append({
                    'user': appt.patient,
                    'type': Notification.TYPE_CONFIRMED,
                    'title': 'Görüş Təsdiqləndi',
                    'message': (
                        f'Dr. {appt.doctor.last_name} ilə görüşünüz təsdiqləndi. '
                        f'Tarix: {appt.starts_at.strftime("%d.%m.%Y %H:%M")}.'
                    ),
                    'is_read': False,
                })
            elif appt.status == Appointment.STATUS_CANCELLED:
                entries.append({
                    'user': appt.patient,
                    'type': Notification.TYPE_CANCELLED,
                    'title': 'Görüş Ləğv Edildi',
                    'message': (
                        f'Dr. {appt.doctor.last_name} ilə '
                        f'{appt.starts_at.strftime("%d.%m.%Y")} tarixindəki '
                        'görüşünüz ləğv edildi.'
                    ),
                    'is_read': True,
                })
            elif appt.status == Appointment.STATUS_REQUIRES_RESCHEDULING:
                entries.append({
                    'user': appt.patient,
                    'type': Notification.TYPE_RESCHEDULING,
                    'title': 'Yenidən Planlaşdırma Lazımdır',
                    'message': (
                        f'Dr. {appt.doctor.last_name} ilə görüşünüzü '
                        'yenidən planlaşdırmanız xahiş olunur.'
                    ),
                    'is_read': False,
                })
            elif appt.status == Appointment.STATUS_COMPLETED:
                entries.append({
                    'user': appt.doctor,
                    'type': Notification.TYPE_GENERAL,
                    'title': 'Görüş Tamamlandı',
                    'message': (
                        f'{appt.patient.first_name} {appt.patient.last_name} '
                        'ilə görüş uğurla tamamlandı.'
                    ),
                    'is_read': True,
                })

            for entry in entries:
                _, created = Notification.objects.get_or_create(
                    user=entry['user'],
                    appointment=appt,
                    type=entry['type'],
                    defaults=entry,
                )
                if created:
                    created_count += 1

        return created_count
