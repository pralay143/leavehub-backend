from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from accounts.models import User
from leaves.models import LeaveBalance, LeaveRequest, LeaveType, PublicHoliday
from leaves.services import (
    LeaveRequestError,
    approve_leave_request,
    cancel_leave_request,
    count_leave_days,
    create_leave_request,
    eligible_approvers,
    reject_leave_request,
)

YEAR = 2030
MON = date(2030, 1, 7)  # Monday
TUE, WED, THU, FRI, SAT, SUN = (date(2030, 1, d) for d in range(8, 14))
NEXT_MON = date(2030, 1, 14)
TODAY = date(2030, 1, 1)


def make_user(username, role=User.Role.EMPLOYEE, manager=None, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="Xy7!long-pass",
        role=role,
        manager=manager,
        **extra,
    )


class LeaveServiceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Leave types come from the seed data migration.
        cls.earned = LeaveType.objects.get(code="EARNED")
        cls.sick = LeaveType.objects.get(code="SICK")
        cls.unpaid = LeaveType.objects.get(code="UNPAID")

        cls.hr = make_user("hr", role=User.Role.HR)
        cls.manager = make_user("manager", role=User.Role.MANAGER, manager=cls.hr)
        cls.other_manager = make_user("other_manager", role=User.Role.MANAGER, manager=cls.hr)
        cls.employee = make_user("employee", manager=cls.manager)
        cls.superuser = make_user("admin", is_superuser=True, is_staff=True)

        for user in (cls.hr, cls.manager, cls.other_manager, cls.employee, cls.superuser):
            for leave_type in (cls.earned, cls.sick):
                LeaveBalance.objects.create(
                    employee=user,
                    leave_type=leave_type,
                    year=YEAR,
                    allocated=leave_type.default_annual_days,
                )

    def setUp(self):
        # Fix "today" so the cancel-before-start rule is deterministic.
        patcher = mock.patch("leaves.services.timezone.localdate", return_value=TODAY)
        self.mock_today = patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, start=MON, end=None, leave_type=None, employee=None, half_day=False):
        return create_leave_request(
            employee=employee or self.employee,
            leave_type=leave_type or self.earned,
            start_date=start,
            end_date=end or start,
            half_day=half_day,
            reason="Test",
        )

    def balance(self, user=None, leave_type=None):
        return LeaveBalance.objects.get(
            employee=user or self.employee, leave_type=leave_type or self.earned, year=YEAR
        )

    def assertRuleError(self, message_part, func, *args, **kwargs):
        with self.assertRaises(LeaveRequestError) as ctx:
            func(*args, **kwargs)
        self.assertIn(message_part, ctx.exception.message)


class DayCountTests(LeaveServiceTestCase):
    def test_counts_weekdays_only(self):
        self.assertEqual(count_leave_days(MON, SUN), Decimal("5"))
        self.assertEqual(count_leave_days(FRI, NEXT_MON), Decimal("2"))
        self.assertEqual(self.request(MON, SUN).days, Decimal("5"))

    def test_half_day_counts_as_half(self):
        leave = self.request(WED, half_day=True)
        self.assertEqual(leave.days, Decimal("0.5"))

    def test_weekend_only_range_is_rejected(self):
        self.assertRuleError("no working days", self.request, SAT, SUN)

    def test_half_day_on_weekend_is_rejected(self):
        self.assertRuleError("no working days", self.request, SAT, half_day=True)

    def test_half_day_must_be_single_date(self):
        self.assertRuleError("same date", self.request, MON, TUE, half_day=True)

    def test_end_before_start_is_rejected(self):
        self.assertRuleError("before the start date", self.request, TUE, MON)

    def test_request_spanning_two_years_is_rejected(self):
        self.assertRuleError(
            "cannot span two years", self.request, date(2030, 12, 30), date(2031, 1, 2)
        )

    def test_inactive_leave_type_is_rejected(self):
        LeaveType.objects.filter(pk=self.earned.pk).update(is_active=False)
        self.assertRuleError("not available", self.request)

    def test_public_holidays_are_not_counted(self):
        PublicHoliday.objects.create(date=WED, name="Test holiday")
        self.assertEqual(count_leave_days(MON, FRI), Decimal("4"))
        leave = self.request(MON, FRI)
        self.assertEqual(leave.days, Decimal("4"))
        approve_leave_request(leave_request=leave, reviewer=self.manager)
        self.assertEqual(self.balance().used, Decimal("4"))

    def test_range_of_only_holidays_and_weekend_is_rejected(self):
        PublicHoliday.objects.create(date=FRI, name="Test holiday")
        self.assertRuleError("no working days", self.request, FRI, SUN)

    def test_half_day_on_holiday_is_rejected(self):
        PublicHoliday.objects.create(date=WED, name="Test holiday")
        self.assertRuleError("no working days", self.request, WED, half_day=True)

    def test_existing_request_keeps_its_days_when_a_holiday_is_added(self):
        leave = self.request(MON, FRI)
        PublicHoliday.objects.create(date=WED, name="Late holiday")
        leave.refresh_from_db()
        self.assertEqual(leave.days, Decimal("5"))


class OverlapTests(LeaveServiceTestCase):
    def test_overlap_with_pending_request_is_rejected(self):
        self.request(MON, WED)
        self.assertRuleError("overlap", self.request, WED, FRI)

    def test_overlap_with_approved_request_is_rejected(self):
        leave = self.request(MON, WED)
        approve_leave_request(leave_request=leave, reviewer=self.manager)
        self.assertRuleError("overlap", self.request, TUE, leave_type=self.unpaid)

    def test_two_half_days_on_same_date_overlap(self):
        self.request(WED, half_day=True)
        self.assertRuleError("overlap", self.request, WED, half_day=True)

    def test_cancelled_and_rejected_requests_do_not_block(self):
        cancelled = self.request(MON)
        cancel_leave_request(leave_request=cancelled, user=self.employee)
        rejected = self.request(MON)
        reject_leave_request(leave_request=rejected, reviewer=self.manager, comment="No")
        self.assertEqual(self.request(MON).status, LeaveRequest.Status.PENDING)

    def test_adjacent_dates_do_not_overlap(self):
        self.request(MON, TUE)
        self.request(WED, THU)


class BalanceTests(LeaveServiceTestCase):
    def test_missing_balance_is_rejected(self):
        self.assertRuleError(
            "No Earned Leave balance allocated for 2031", self.request, date(2031, 1, 6)
        )

    def test_request_cannot_exceed_available_including_pending(self):
        LeaveBalance.objects.filter(pk=self.balance().pk).update(allocated=Decimal("6"))
        self.request(MON, FRI)  # 5 days pending
        self.assertRuleError("Insufficient Earned Leave balance", self.request, NEXT_MON, date(2030, 1, 15))

    def test_request_using_exactly_available_days_is_allowed(self):
        LeaveBalance.objects.filter(pk=self.balance().pk).update(allocated=Decimal("5.5"))
        self.request(MON, FRI)
        self.assertEqual(self.request(NEXT_MON, half_day=True).days, Decimal("0.5"))

    def test_unpaid_leave_needs_no_balance(self):
        leave = self.request(MON, FRI, leave_type=self.unpaid)
        approve_leave_request(leave_request=leave, reviewer=self.manager)
        self.assertFalse(LeaveBalance.objects.filter(leave_type=self.unpaid).exists())

    def test_approval_adds_days_to_used(self):
        leave = self.request(MON, WED)
        approve_leave_request(leave_request=leave, reviewer=self.manager)
        self.assertEqual(self.balance().used, Decimal("3"))

    def test_approval_rechecks_balance(self):
        first = self.request(MON, FRI)  # 5 days
        second = self.request(NEXT_MON, date(2030, 1, 18))  # 5 days
        # HR lowers the allocation after both were submitted.
        LeaveBalance.objects.filter(pk=self.balance().pk).update(allocated=Decimal("8"))

        approve_leave_request(leave_request=first, reviewer=self.manager)
        self.assertRuleError(
            "Insufficient Earned Leave balance to approve",
            approve_leave_request,
            leave_request=second,
            reviewer=self.manager,
        )
        second.refresh_from_db()
        self.assertEqual(second.status, LeaveRequest.Status.PENDING)
        self.assertEqual(self.balance().used, Decimal("5"))


class ApproverTests(LeaveServiceTestCase):
    def test_manager_approves_direct_report(self):
        leave = approve_leave_request(leave_request=self.request(), reviewer=self.manager)
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(leave.reviewed_by, self.manager)
        self.assertIsNotNone(leave.reviewed_at)

    def test_other_manager_cannot_approve(self):
        self.assertRuleError(
            "not allowed", approve_leave_request, leave_request=self.request(), reviewer=self.other_manager
        )

    def test_hr_can_override(self):
        leave = approve_leave_request(leave_request=self.request(), reviewer=self.hr)
        self.assertEqual(leave.reviewed_by, self.hr)

    def test_superuser_can_approve(self):
        leave = approve_leave_request(leave_request=self.request(), reviewer=self.superuser)
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)

    def test_nobody_can_approve_own_request(self):
        own = self.request(employee=self.hr)
        self.assertRuleError("your own", approve_leave_request, leave_request=own, reviewer=self.hr)

    def test_inactive_reviewer_cannot_approve(self):
        User.objects.filter(pk=self.manager.pk).update(is_active=False)
        self.manager.refresh_from_db()
        self.assertRuleError(
            "not allowed", approve_leave_request, leave_request=self.request(), reviewer=self.manager
        )

    def test_routing_to_manager(self):
        self.assertEqual(list(eligible_approvers(self.employee)), [self.manager])

    def test_routing_hr_user_goes_to_other_hr(self):
        other_hr = make_user("hr2", role=User.Role.HR)
        self.assertEqual(list(eligible_approvers(self.hr)), [other_hr])

    def test_routing_hr_user_falls_back_to_superuser(self):
        self.assertEqual(list(eligible_approvers(self.hr)), [self.superuser])

    def test_routing_no_manager_goes_to_hr(self):
        loner = make_user("loner")
        self.assertEqual(list(eligible_approvers(loner)), [self.hr])

    def test_routing_inactive_manager_falls_back_to_hr(self):
        User.objects.filter(pk=self.manager.pk).update(is_active=False)
        self.assertEqual(list(eligible_approvers(self.employee)), [self.hr])

    def test_request_rejected_when_no_eligible_approver(self):
        User.objects.filter(pk=self.superuser.pk).update(is_active=False)
        self.assertRuleError("No one is available to approve", self.request, employee=self.hr)

    def test_inactive_employee_cannot_request(self):
        User.objects.filter(pk=self.employee.pk).update(is_active=False)
        self.assertRuleError("Inactive users", self.request)


class StatusTransitionTests(LeaveServiceTestCase):
    def test_cannot_approve_non_pending_request(self):
        leave = approve_leave_request(leave_request=self.request(), reviewer=self.manager)
        self.assertRuleError(
            "Only pending requests", approve_leave_request, leave_request=leave, reviewer=self.hr
        )
        self.assertEqual(self.balance().used, Decimal("1"))

    def test_cannot_reject_approved_request(self):
        leave = approve_leave_request(leave_request=self.request(), reviewer=self.manager)
        self.assertRuleError(
            "Only pending requests",
            reject_leave_request,
            leave_request=leave,
            reviewer=self.manager,
            comment="Too late",
        )

    def test_reject_requires_comment(self):
        leave = self.request()
        self.assertRuleError(
            "comment is required", reject_leave_request, leave_request=leave, reviewer=self.manager, comment="  "
        )

    def test_reject_records_review_and_leaves_balance(self):
        leave = reject_leave_request(
            leave_request=self.request(), reviewer=self.manager, comment="Busy week"
        )
        self.assertEqual(leave.status, LeaveRequest.Status.REJECTED)
        self.assertEqual(leave.review_comment, "Busy week")
        self.assertEqual(self.balance().used, Decimal("0"))

    def test_cancel_pending_request(self):
        leave = cancel_leave_request(leave_request=self.request(), user=self.employee)
        self.assertEqual(leave.status, LeaveRequest.Status.CANCELLED)

    def test_cancel_approved_future_request_returns_days(self):
        leave = approve_leave_request(leave_request=self.request(MON, WED), reviewer=self.manager)
        self.assertEqual(self.balance().used, Decimal("3"))
        cancel_leave_request(leave_request=leave, user=self.employee)
        self.assertEqual(self.balance().used, Decimal("0"))

    def test_cannot_cancel_approved_request_on_or_after_start(self):
        leave = approve_leave_request(leave_request=self.request(MON, WED), reviewer=self.manager)
        self.mock_today.return_value = MON
        self.assertRuleError(
            "before its start date", cancel_leave_request, leave_request=leave, user=self.employee
        )
        self.assertEqual(self.balance().used, Decimal("3"))

    def test_only_employee_can_cancel(self):
        leave = self.request()
        for other in (self.manager, self.hr, self.superuser):
            self.assertRuleError(
                "Only the employee", cancel_leave_request, leave_request=leave, user=other
            )

    def test_cannot_cancel_rejected_or_cancelled_request(self):
        rejected = reject_leave_request(
            leave_request=self.request(MON), reviewer=self.manager, comment="No"
        )
        self.assertRuleError(
            "rejected request cannot be cancelled",
            cancel_leave_request,
            leave_request=rejected,
            user=self.employee,
        )
        cancelled = cancel_leave_request(leave_request=self.request(TUE), user=self.employee)
        self.assertRuleError(
            "cancelled request cannot be cancelled",
            cancel_leave_request,
            leave_request=cancelled,
            user=self.employee,
        )
