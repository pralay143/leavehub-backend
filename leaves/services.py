"""Leave request workflow: create, approve, reject and cancel.

Every operation runs in a single transaction and takes row locks in a fixed
order (employee user row, then leave request, then leave balance) so that
concurrent operations cannot double-spend a balance or deadlock.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import LeaveBalance, LeaveRequest, LeaveType

HALF_DAY = Decimal("0.5")
ACTIVE_STATUSES = (LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED)


class LeaveRequestError(Exception):
    """A leave request rule was violated. The message is safe to show to users."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# --- Calculations -----------------------------------------------------------


def count_leave_days(start_date, end_date, half_day=False):
    """Working days (Monday to Friday) from start_date to end_date inclusive."""
    if half_day:
        return HALF_DAY if start_date.weekday() < 5 else Decimal("0")
    days = 0
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return Decimal(days)


def pending_days(employee, leave_type, year, exclude_request=None):
    """Days in the employee's pending requests for a leave type and year."""
    requests = LeaveRequest.objects.filter(
        employee=employee,
        leave_type=leave_type,
        status=LeaveRequest.Status.PENDING,
        start_date__year=year,
    )
    if exclude_request is not None:
        requests = requests.exclude(pk=exclude_request.pk)
    return requests.aggregate(total=Sum("days"))["total"] or Decimal("0")


def available_days(balance):
    """Allocated minus used minus pending days for a balance."""
    return (
        balance.allocated
        - balance.used
        - pending_days(balance.employee_id, balance.leave_type_id, balance.year)
    )


# --- Approvers --------------------------------------------------------------


def eligible_approvers(employee):
    """Users whose queue a request from `employee` is routed to.

    - Not HR, with an active manager: the manager.
    - HR, or no active manager: other active HR users.
    - No other active HR users: other active superusers.
    The employee is never their own approver.
    """
    User = get_user_model()
    others = User.objects.filter(is_active=True).exclude(pk=employee.pk)

    if employee.role != User.Role.HR and employee.manager_id is not None:
        manager = others.filter(pk=employee.manager_id)
        if manager.exists():
            return manager

    hr_users = others.filter(role=User.Role.HR)
    if hr_users.exists():
        return hr_users
    return others.filter(is_superuser=True)


def review_queue_filter(reviewer):
    """A LeaveRequest filter matching requests routed to `reviewer`.

    The queryset form of `eligible_approvers`: a request matches when
    `reviewer` is in `eligible_approvers(request.employee)`. Combine it with
    a status filter to build a review inbox.
    """
    User = get_user_model()
    if not reviewer.is_active:
        return Q(pk__in=[])

    # Mirrors eligible_approvers: requests go to an active manager unless the
    # employee is HR; everything else goes to HR, then to superusers.
    to_manager = ~Q(employee__role=User.Role.HR) & Q(employee__manager__is_active=True)
    to_hr = (
        Q(employee__role=User.Role.HR)
        | Q(employee__manager__isnull=True)
        | Q(employee__manager__is_active=False)
    )

    routed = to_manager & Q(employee__manager=reviewer)
    if reviewer.role == User.Role.HR:
        routed |= to_hr
    elif reviewer.is_superuser:
        # Superusers are the fallback when no active HR user other than the
        # employee exists.
        active_hr = list(User.objects.filter(is_active=True, role=User.Role.HR).values_list("pk", flat=True))
        if not active_hr:
            routed |= to_hr
        elif len(active_hr) == 1:
            routed |= to_hr & Q(employee_id=active_hr[0])
    return routed & ~Q(employee=reviewer)


def can_review(reviewer, leave_request):
    """Whether `reviewer` may approve or reject `leave_request`.

    Allowed: the employee's manager, any HR user (override) or any superuser,
    provided they are active. Nobody may review their own request.
    """
    User = get_user_model()
    if not reviewer.is_active or reviewer.pk == leave_request.employee_id:
        return False
    return (
        reviewer.is_superuser
        or reviewer.role == User.Role.HR
        or leave_request.employee.manager_id == reviewer.pk
    )


# --- Locking helpers --------------------------------------------------------


def _lock_employee(employee_id):
    return get_user_model().objects.select_for_update().get(pk=employee_id)


def _lock_request(leave_request_id):
    return (
        LeaveRequest.objects.select_for_update(of=("self",))
        .select_related("employee", "leave_type")
        .get(pk=leave_request_id)
    )


def _lock_balance(employee, leave_type, year):
    return (
        LeaveBalance.objects.select_for_update()
        .filter(employee=employee, leave_type=leave_type, year=year)
        .first()
    )


# --- Operations -------------------------------------------------------------


def create_leave_request(*, employee, leave_type, start_date, end_date, half_day=False, reason=""):
    with transaction.atomic():
        employee = _lock_employee(employee.pk)
        if not employee.is_active:
            raise LeaveRequestError("Inactive users cannot request leave.")

        leave_type = LeaveType.objects.get(pk=leave_type.pk)
        if not leave_type.is_active:
            raise LeaveRequestError(f"{leave_type.name} is not available.")

        if start_date > end_date:
            raise LeaveRequestError("The end date cannot be before the start date.")
        if start_date.year != end_date.year:
            raise LeaveRequestError(
                "A request cannot span two years. Submit a separate request for each year."
            )
        if half_day and start_date != end_date:
            raise LeaveRequestError("A half day must start and end on the same date.")

        days = count_leave_days(start_date, end_date, half_day)
        if days <= 0:
            raise LeaveRequestError("The selected dates contain no working days.")

        overlapping = LeaveRequest.objects.filter(
            employee=employee,
            status__in=ACTIVE_STATUSES,
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        if overlapping.exists():
            raise LeaveRequestError(
                "These dates overlap another pending or approved leave request."
            )

        if leave_type.requires_balance:
            year = start_date.year
            balance = _lock_balance(employee, leave_type, year)
            if balance is None:
                raise LeaveRequestError(f"No {leave_type.name} balance allocated for {year}.")
            available = available_days(balance)
            if days > available:
                raise LeaveRequestError(
                    f"Insufficient {leave_type.name} balance: {available} day(s) available "
                    f"(after pending requests), {days} requested."
                )

        if not eligible_approvers(employee).exists():
            raise LeaveRequestError(
                "No one is available to approve this request. Contact HR or an administrator."
            )

        return LeaveRequest.objects.create(
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            half_day=half_day,
            days=days,
            reason=reason,
        )


def approve_leave_request(*, leave_request, reviewer, comment=""):
    with transaction.atomic():
        _lock_employee(leave_request.employee_id)
        leave_request = _lock_request(leave_request.pk)
        _ensure_pending(leave_request)
        _ensure_can_review(reviewer, leave_request)

        leave_type = leave_request.leave_type
        if leave_type.requires_balance:
            balance = _lock_balance(
                leave_request.employee_id, leave_type, leave_request.start_date.year
            )
            if balance is None:
                raise LeaveRequestError(
                    f"No {leave_type.name} balance allocated for {leave_request.start_date.year}."
                )
            remaining = balance.allocated - balance.used
            if leave_request.days > remaining:
                raise LeaveRequestError(
                    f"Insufficient {leave_type.name} balance to approve: {remaining} day(s) "
                    f"remaining, {leave_request.days} requested."
                )
            balance.used += leave_request.days
            balance.save(update_fields=["used"])

        _record_review(leave_request, reviewer, LeaveRequest.Status.APPROVED, comment)
        return leave_request


def reject_leave_request(*, leave_request, reviewer, comment):
    if not (comment or "").strip():
        raise LeaveRequestError("A comment is required when rejecting a request.")

    with transaction.atomic():
        _lock_employee(leave_request.employee_id)
        leave_request = _lock_request(leave_request.pk)
        _ensure_pending(leave_request)
        _ensure_can_review(reviewer, leave_request)

        _record_review(leave_request, reviewer, LeaveRequest.Status.REJECTED, comment)
        return leave_request


def cancel_leave_request(*, leave_request, user):
    with transaction.atomic():
        _lock_employee(leave_request.employee_id)
        leave_request = _lock_request(leave_request.pk)

        if user.pk != leave_request.employee_id:
            raise LeaveRequestError("Only the employee who made the request can cancel it.")

        if leave_request.status == LeaveRequest.Status.APPROVED:
            if leave_request.start_date <= timezone.localdate():
                raise LeaveRequestError(
                    "Approved leave can only be cancelled before its start date."
                )
            leave_type = leave_request.leave_type
            if leave_type.requires_balance:
                balance = _lock_balance(
                    leave_request.employee_id, leave_type, leave_request.start_date.year
                )
                if balance is not None:
                    balance.used -= leave_request.days
                    balance.save(update_fields=["used"])
        elif leave_request.status != LeaveRequest.Status.PENDING:
            raise LeaveRequestError(
                f"A {leave_request.get_status_display().lower()} request cannot be cancelled."
            )

        leave_request.status = LeaveRequest.Status.CANCELLED
        leave_request.save(update_fields=["status", "updated_at"])
        return leave_request


# --- Yearly allocation ------------------------------------------------------


@dataclass
class AllocationResult:
    year: int
    balances: list
    active_users: int
    leave_types: int
    already_existed: int
    dry_run: bool

    @property
    def created(self):
        return 0 if self.dry_run else len(self.balances)


def allocate_leave_balances(year, *, dry_run=False):
    """Create missing balances for `year`: one per active user and active leave
    type that uses a balance, allocated from the type's default_annual_days.
    Existing balances are never changed, so this is safe to run repeatedly."""
    if not 2000 <= year <= 2100:
        raise LeaveRequestError("The year must be between 2000 and 2100.")

    users = list(get_user_model().objects.filter(is_active=True).order_by("email"))
    leave_types = list(LeaveType.objects.filter(is_active=True, requires_balance=True))
    existing = set(
        LeaveBalance.objects.filter(year=year).values_list("employee_id", "leave_type_id")
    )
    to_create = [
        LeaveBalance(
            employee=user,
            leave_type=leave_type,
            year=year,
            allocated=leave_type.default_annual_days,
        )
        for user in users
        for leave_type in leave_types
        if (user.pk, leave_type.pk) not in existing
    ]

    if not dry_run:
        with transaction.atomic():
            # ignore_conflicts: a balance created concurrently is left as-is.
            LeaveBalance.objects.bulk_create(to_create, ignore_conflicts=True)

    return AllocationResult(
        year=year,
        balances=to_create,
        active_users=len(users),
        leave_types=len(leave_types),
        already_existed=len(existing),
        dry_run=dry_run,
    )


# --- Internal checks --------------------------------------------------------


def _ensure_pending(leave_request):
    if leave_request.status != LeaveRequest.Status.PENDING:
        raise LeaveRequestError(
            f"Only pending requests can be reviewed; this one is "
            f"{leave_request.get_status_display().lower()}."
        )


def _ensure_can_review(reviewer, leave_request):
    if reviewer.pk == leave_request.employee_id:
        raise LeaveRequestError("You cannot review your own leave request.")
    if not can_review(reviewer, leave_request):
        raise LeaveRequestError("You are not allowed to review this leave request.")


def _record_review(leave_request, reviewer, status, comment):
    leave_request.status = status
    leave_request.reviewed_by = reviewer
    leave_request.reviewed_at = timezone.now()
    leave_request.review_comment = (comment or "").strip()
    leave_request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "review_comment", "updated_at"]
    )
