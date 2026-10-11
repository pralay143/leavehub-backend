from decimal import Decimal

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q


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
