from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q


class LeaveType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[
            RegexValidator(
                r"^[A-Z][A-Z0-9_]*$",
                "Use uppercase letters, digits and underscores, starting with a letter.",
            )
        ],
    )
    default_annual_days = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Days allocated per year when yearly balances are created.",
    )
    is_paid = models.BooleanField(default=True)
    requires_balance = models.BooleanField(
        default=True,
        help_text="If unchecked, requests are not limited or deducted from a balance.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(default_annual_days__gte=0),
                name="leaves_leavetype_default_days_non_negative",
            ),
        ]

    def __str__(self):
        return self.name


class LeaveBalance(models.Model):
    """Days allocated to and used by one employee for one leave type in one year.

    `used` is changed only by the leave request service (approve and cancel),
    under a row lock. Pending days are calculated from pending requests, not
    stored here.
    """

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leave_balances",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="balances",
    )
    year = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
    )
    allocated = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0"))],
    )
    used = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["-year", "employee__email", "leave_type__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "leave_type", "year"],
                name="leaves_leavebalance_unique_employee_type_year",
            ),
            models.CheckConstraint(
                condition=Q(allocated__gte=0),
                name="leaves_leavebalance_allocated_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(used__gte=0),
                name="leaves_leavebalance_used_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(allocated__gte=F("used")),
                name="leaves_leavebalance_allocated_not_below_used",
                violation_error_message="Allocated days cannot be less than days already used.",
            ),
        ]

    def __str__(self):
        return f"{self.employee} - {self.leave_type} ({self.year})"

    def clean(self):
        super().clean()
        # Also enforced by a DB check constraint. Checked here explicitly because
        # model forms skip constraint validation for fields not on the form, and
        # `used` is read-only in the admin.
        if self.allocated is not None and self.used is not None and self.allocated < self.used:
            raise ValidationError(
                {
                    "allocated": (
                        f"Allocated days cannot be less than days already used ({self.used})."
                    )
                }
            )

    @property
    def remaining(self):
        """Allocated minus used. Pending requests are not subtracted here."""
        return self.allocated - self.used


class LeaveRequest(models.Model):
    """A request for leave. Create and change it only through leaves.services."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="leave_requests",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="requests",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    half_day = models.BooleanField(default=False)
    days = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        help_text="Working days requested, calculated by the server.",
    )
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_leave_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["employee", "status"], name="leaves_req_employee_status"),
            models.Index(fields=["status"], name="leaves_req_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="leaves_leaverequest_end_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(days__gt=0),
                name="leaves_leaverequest_days_positive",
            ),
            models.CheckConstraint(
                condition=Q(half_day=False) | Q(start_date=F("end_date"), days=Decimal("0.5")),
                name="leaves_leaverequest_half_day_single_date",
            ),
        ]

    def __str__(self):
        return f"{self.employee} - {self.leave_type} {self.start_date}..{self.end_date} ({self.status})"


class PublicHoliday(models.Model):
    """A company-wide public holiday. Holidays are not counted as leave days."""

    date = models.DateField(unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.name} ({self.date})"
