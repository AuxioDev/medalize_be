from unittest.mock import patch

from django.test import TestCase

from apps.doctors.models import Workplace
from apps.hospitals.models import Hospital, HospitalDoctorLink
from apps.hospitals.services import resolve_merge
from apps.users.models import User


class MergeTests(TestCase):
    def setUp(self):
        self.target = Hospital.objects.create(
            name='Target Hospital', city='baku', status=Hospital.STATUS_CONFIRMED,
        )
        self.source = Hospital.objects.create(
            name='Duplicate Hospital', city='baku', status=Hospital.STATUS_PENDING_REVIEW,
        )
        self.doctor1 = User.objects.create_user(
            email='d1@test.com', password='Pass1234', role=User.ROLE_DOCTOR,
            first_name='Doc', last_name='One',
        )
        self.doctor2 = User.objects.create_user(
            email='d2@test.com', password='Pass1234', role=User.ROLE_DOCTOR,
            first_name='Doc', last_name='Two',
        )

    def _mark_merged(self):
        self.source.merged_into = self.target
        self.source.status = Hospital.STATUS_MERGED
        self.source.save()

    def test_merge_repoints_workplaces_and_links(self):
        wp = Workplace.objects.create(
            doctor=self.doctor1, hospital=self.source, name='X', address='Y',
            city='baku', region='baku', type='hospital',
        )
        link = HospitalDoctorLink.objects.create(
            hospital=self.source, doctor=self.doctor1, status=HospitalDoctorLink.STATUS_CONFIRMED,
            requested_by=HospitalDoctorLink.REQUESTED_BY_DOCTOR,
        )

        self._mark_merged()
        resolve_merge(self.source)

        wp.refresh_from_db()
        self.assertEqual(wp.hospital_id, self.target.id)
        link.refresh_from_db()
        self.assertEqual(link.hospital_id, self.target.id)
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_CONFIRMED)

    def test_merge_collision_keeps_the_stronger_link_status(self):
        # doctor2 is CONFIRMED at the target already, but only PENDING at
        # the source — the target's stronger status must survive the merge.
        HospitalDoctorLink.objects.create(
            hospital=self.target, doctor=self.doctor2, status=HospitalDoctorLink.STATUS_CONFIRMED,
            requested_by=HospitalDoctorLink.REQUESTED_BY_DOCTOR,
        )
        HospitalDoctorLink.objects.create(
            hospital=self.source, doctor=self.doctor2, status=HospitalDoctorLink.STATUS_PENDING,
            requested_by=HospitalDoctorLink.REQUESTED_BY_DOCTOR,
        )

        self._mark_merged()
        resolve_merge(self.source)

        remaining = HospitalDoctorLink.objects.filter(doctor=self.doctor2)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().hospital_id, self.target.id)
        self.assertEqual(remaining.first().status, HospitalDoctorLink.STATUS_CONFIRMED)

    def test_merge_collision_weaker_source_status_is_dropped(self):
        # Reverse case: target has the stronger PENDING row, source has a
        # REJECTED one — target's row wins, source's is simply dropped.
        target_link = HospitalDoctorLink.objects.create(
            hospital=self.target, doctor=self.doctor1, status=HospitalDoctorLink.STATUS_PENDING,
            requested_by=HospitalDoctorLink.REQUESTED_BY_DOCTOR,
        )
        HospitalDoctorLink.objects.create(
            hospital=self.source, doctor=self.doctor1, status=HospitalDoctorLink.STATUS_REJECTED,
            requested_by=HospitalDoctorLink.REQUESTED_BY_DOCTOR,
        )

        self._mark_merged()
        resolve_merge(self.source)

        remaining = HospitalDoctorLink.objects.filter(doctor=self.doctor1)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().id, target_link.id)

    def test_merge_into_self_is_a_noop(self):
        self.source.merged_into = self.source
        resolve_merge(self.source)  # must not raise / must not touch anything
        self.assertEqual(Hospital.objects.filter(pk=self.source.pk).count(), 1)


class ApproveClaimNotificationTests(TestCase):
    def test_approve_claim_fires_hospital_approved_notification(self):
        owner = User.objects.create_user(
            email='owner@test.com', password='Pass1234', role=User.ROLE_HOSPITAL,
            first_name='Own', last_name='Er',
        )
        hospital = Hospital.objects.create(
            name='Approve Me', city='baku', owner=owner,
            claim_status=Hospital.CLAIM_PENDING, status=Hospital.STATUS_PENDING_REVIEW,
        )
        # Re-fetch so Hospital.from_db actually runs and sets
        # _original_claim_status — a freshly Hospital.objects.create()'d
        # instance never goes through from_db(), same caveat documented in
        # apps.subscriptions.signals.start_trial_on_verification. This
        # mirrors what the admin action does: it iterates a queryset, which
        # is always freshly fetched.
        hospital = Hospital.objects.get(pk=hospital.pk)

        with patch('apps.notifications.tasks.send_hospital_notification.delay') as mock_delay:
            hospital.claim_status = Hospital.CLAIM_APPROVED
            hospital.status = Hospital.STATUS_CONFIRMED
            hospital.save()

        mock_delay.assert_called_once_with(str(owner.id), 'hospital_approved')

    def test_reject_claim_fires_hospital_rejected_notification(self):
        owner = User.objects.create_user(
            email='owner2@test.com', password='Pass1234', role=User.ROLE_HOSPITAL,
            first_name='Own', last_name='Er',
        )
        hospital = Hospital.objects.create(
            name='Reject Me', city='baku', owner=owner, claim_status=Hospital.CLAIM_PENDING,
        )
        hospital = Hospital.objects.get(pk=hospital.pk)

        with patch('apps.notifications.tasks.send_hospital_notification.delay') as mock_delay:
            hospital.claim_status = Hospital.CLAIM_REJECTED
            hospital.save()

        mock_delay.assert_called_once_with(str(owner.id), 'hospital_rejected')

    def test_unrelated_field_change_does_not_fire_a_notification(self):
        owner = User.objects.create_user(
            email='owner3@test.com', password='Pass1234', role=User.ROLE_HOSPITAL,
            first_name='Own', last_name='Er',
        )
        hospital = Hospital.objects.create(
            name='Untouched Claim', city='baku', owner=owner, claim_status=Hospital.CLAIM_APPROVED,
        )
        hospital = Hospital.objects.get(pk=hospital.pk)

        with patch('apps.notifications.tasks.send_hospital_notification.delay') as mock_delay:
            hospital.phone = '+994501234567'
            hospital.save()

        mock_delay.assert_not_called()


class OwnerDeletionTests(TestCase):
    def test_deleting_owner_resets_claim_status_and_keeps_the_registry_entry(self):
        owner = User.objects.create_user(
            email='doomed@test.com', password='Pass1234', role=User.ROLE_HOSPITAL,
            first_name='Doomed', last_name='Owner',
        )
        hospital = Hospital.objects.create(
            name='Survives Owner Deletion', city='baku', owner=owner,
            claim_status=Hospital.CLAIM_APPROVED, status=Hospital.STATUS_CONFIRMED,
        )

        owner.delete()

        hospital.refresh_from_db()
        self.assertIsNone(hospital.owner_id)
        self.assertEqual(hospital.claim_status, Hospital.CLAIM_NONE)
        # The registry entry itself survives — other doctors' workplaces
        # may still point at it.
        self.assertTrue(Hospital.objects.filter(pk=hospital.pk).exists())
