from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower


def normalize_email(email):
    """Lowercase and trim an email address (the whole address, not just the domain)."""
    return (email or "").strip().lower()


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserManager(DjangoUserManager):
    """User manager that logs in by email, case-insensitively."""

    def get_by_natural_key(self, email):
        return self.get(email=normalize_email(email))

    async def aget_by_natural_key(self, email):
        return await self.aget(email=normalize_email(email))

    def _create_user_object(self, username, email, password, **extra_fields):
        return super()._create_user_object(
            username, normalize_email(email), password, **extra_fields
        )


class User(AbstractUser):
    """Custom user model for LeaveHub.

    Users log in with their email address. Emails are stored lowercase and
    are unique regardless of case.
    """

    class Role(models.TextChoices):
        EMPLOYEE = "EMPLOYEE", "Employee"
        MANAGER = "MANAGER", "Manager"
        HR = "HR", "HR"

    APPROVER_ROLES = (Role.MANAGER, Role.HR)

    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_reports",
        help_text="Approves this user's leave requests. Must be a Manager or HR user.",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="members",
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
            models.CheckConstraint(
                condition=~Q(manager=F("pk")),
                name="accounts_user_not_own_manager",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.email = normalize_email(self.email)
        errors = {}

        if self.manager_id is not None:
            if self.pk is not None and self.manager_id == self.pk:
                errors["manager"] = "A user cannot be their own manager."
            elif self.manager.role not in self.APPROVER_ROLES:
                errors["manager"] = "The manager must have the Manager or HR role."

        if (
            self.pk is not None
            and self.role not in self.APPROVER_ROLES
            and self.direct_reports.exists()
        ):
            errors["role"] = (
                "This user has direct reports. Reassign them before changing "
                "the role to Employee."
            )

        if errors:
            raise ValidationError(errors)
