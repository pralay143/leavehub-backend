from datetime import date
from unittest import mock

from django.conf import settings
from django.core import mail
from django.db import transaction
from django.test import TestCase

from accounts.models import User
from leaves.models import LeaveBalance, LeaveRequest, LeaveType
from leaves.services import (
    LeaveRequestError,
    approve_leave_request,
    cancel_leave_request,
    create_leave_request,
    reject_leave_request,
)

YEAR = 2030
MON = date(2030, 1, 7)  # Monday
WED = date(2030, 1, 9)
TODAY = date(2030, 1, 1)


def make_user(username, role=User.Role.EMPLOYEE, manager=None, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="Xy7!long-pass",
        role=role,
        manager=manager,
        first_name=username.capitalize(),
        last_name="Test",
        **extra,
    )


class NotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.earned = LeaveType.objects.get(code="EARNED")
        cls.hr = make_user("hr", role=User.Role.HR)
        cls.hr2 = make_user("hrtwo", role=User.Role.HR)
        cls.manager = make_user("manager", role=User.Role.MANAGER, manager=cls.hr)
        cls.employee = make_user("employee", manager=cls.manager)
        for user in (cls.hr, cls.hr2, cls.manager, cls.employee):
            LeaveBalance.objects.create(employee=user, leave_type=cls.earned, year=YEAR, allocated=18)

    def setUp(self):
        patcher = mock.patch("leaves.services.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def submit(self, employee=None, start=MON, end=WED):
        """Create a request and run the on-commit callbacks (the emails)."""
        with self.captureOnCommitCallbacks(execute=True):
            return create_leave_request(
                employee=employee or self.employee,
                leave_type=self.earned,
                start_date=start,
                end_date=end,
                reason="Family trip",
            )

    def recipients(self):
        return sorted(address for message in mail.outbox for address in message.to)

    def test_submitted_request_emails_the_manager(self):
        leave = self.submit()
        self.assertEqual(self.recipients(), ["manager@example.com"])
        message = mail.outbox[0]
        self.assertIn("Leave request from Employee Test: 7 Jan – 9 Jan 2030", message.subject)
        self.assertIn("Dates: 7 Jan – 9 Jan 2030 (3 days)", message.body)
        self.assertIn("Reason: Family trip", message.body)
        self.assertIn(f"{settings.FRONTEND_URL}/requests/{leave.pk}", message.body)

    def test_hr_users_request_emails_the_other_hr_users(self):
        self.submit(employee=self.hr)
        self.assertEqual(self.recipients(), ["hrtwo@example.com"])

    def test_request_without_manager_emails_each_hr_user_separately(self):
        User.objects.filter(pk=self.employee.pk).update(manager=None)
        self.submit(employee=User.objects.get(pk=self.employee.pk))
        self.assertEqual(self.recipients(), ["hr@example.com", "hrtwo@example.com"])
        self.assertTrue(all(len(message.to) == 1 for message in mail.outbox))

    def test_approval_emails_the_employee(self):
        leave = self.submit()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            approve_leave_request(leave_request=leave, reviewer=self.manager, comment="Enjoy")
        self.assertEqual(self.recipients(), ["employee@example.com"])
        message = mail.outbox[0]
        self.assertIn("was approved", message.subject)
        self.assertIn("approved by Manager Test", message.body)
        self.assertIn("Comment: Enjoy", message.body)

    def test_rejection_emails_the_employee_with_the_comment(self):
        leave = self.submit()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            reject_leave_request(leave_request=leave, reviewer=self.manager, comment="Release week")
        self.assertEqual(self.recipients(), ["employee@example.com"])
        self.assertIn("was rejected", mail.outbox[0].subject)
        self.assertIn("Comment: Release week", mail.outbox[0].body)

    def test_cancelling_a_pending_request_emails_the_approvers(self):
        leave = self.submit()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            cancel_leave_request(leave_request=leave, user=self.employee)
        self.assertEqual(self.recipients(), ["manager@example.com"])
        self.assertIn("cancelled", mail.outbox[0].subject)
        self.assertIn("still pending", mail.outbox[0].body)

    def test_cancelling_approved_leave_emails_whoever_approved_it(self):
        leave = self.submit()
        with self.captureOnCommitCallbacks(execute=True):
            approve_leave_request(leave_request=leave, reviewer=self.hr)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            cancel_leave_request(leave_request=leave, user=self.employee)
        self.assertEqual(self.recipients(), ["hr@example.com"])
        self.assertIn("days have been returned", mail.outbox[0].body)

    def test_failed_operation_sends_nothing(self):
        self.submit()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True), self.assertRaises(LeaveRequestError):
            create_leave_request(employee=self.employee, leave_type=self.earned, start_date=WED, end_date=WED)
        self.assertEqual(mail.outbox, [])

    def test_rolled_back_transaction_sends_nothing(self):
        class Abort(Exception):
            pass

        with self.captureOnCommitCallbacks(execute=True), self.assertRaises(Abort):
            with transaction.atomic():
                create_leave_request(employee=self.employee, leave_type=self.earned, start_date=MON, end_date=MON)
                raise Abort
        self.assertEqual(mail.outbox, [])
        self.assertFalse(LeaveRequest.objects.exists())

    def test_inactive_users_receive_nothing(self):
        leave = self.submit()
        mail.outbox.clear()
        User.objects.filter(pk=self.employee.pk).update(is_active=False)
        leave.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            approve_leave_request(leave_request=leave, reviewer=self.manager)
        self.assertEqual(mail.outbox, [])

    def test_delivery_failure_does_not_break_the_operation(self):
        leave = self.submit()
        with (
            mock.patch("leaves.notifications.send_mass_mail", side_effect=OSError("SMTP down")),
            self.assertLogs("leaves.notifications", level="ERROR") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            approved = approve_leave_request(leave_request=leave, reviewer=self.manager)
        self.assertEqual(approved.status, LeaveRequest.Status.APPROVED)
        self.assertIn("Could not send approved email", logs.output[0])
