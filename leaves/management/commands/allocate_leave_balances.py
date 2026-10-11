from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from leaves.models import LeaveBalance, LeaveType


class Command(BaseCommand):
    help = (
        "Create missing leave balances for a year: one per active user and "
        "active leave type that uses a balance. Existing balances are never changed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            default=timezone.localdate().year,
            help="Year to allocate (default: the current year).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created without saving anything.",
        )

    def handle(self, *args, year, dry_run, **options):
        if not 2000 <= year <= 2100:
            raise CommandError("--year must be between 2000 and 2100.")

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

        for balance in to_create:
            self.stdout.write(
                f"  {balance.employee.email}: {balance.leave_type.code} = {balance.allocated}"
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: would create {len(to_create)} balance(s) for {year}; nothing saved."
                )
            )
            return

        with transaction.atomic():
            # ignore_conflicts: a balance created concurrently is left as-is.
            LeaveBalance.objects.bulk_create(to_create, ignore_conflicts=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(to_create)} balance(s) for {year} "
                f"({len(users)} active user(s), {len(leave_types)} leave type(s); "
                f"{len(existing)} already existed)."
            )
        )
