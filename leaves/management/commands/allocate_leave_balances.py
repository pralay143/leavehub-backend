from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from leaves.services import LeaveRequestError, allocate_leave_balances


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
        try:
            result = allocate_leave_balances(year, dry_run=dry_run)
        except LeaveRequestError as exc:
            raise CommandError(exc.message)

        for balance in result.balances:
            self.stdout.write(
                f"  {balance.employee.email}: {balance.leave_type.code} = {balance.allocated}"
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: would create {len(result.balances)} balance(s) for {year}; nothing saved."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {result.created} balance(s) for {year} "
                f"({result.active_users} active user(s), {result.leave_types} leave type(s); "
                f"{result.already_existed} already existed)."
            )
        )
