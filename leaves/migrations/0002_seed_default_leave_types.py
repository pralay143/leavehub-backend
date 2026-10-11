from decimal import Decimal

from django.db import migrations

DEFAULT_LEAVE_TYPES = [
    {"code": "EARNED", "name": "Earned Leave", "default_annual_days": Decimal("18"), "is_paid": True, "requires_balance": True},
    {"code": "SICK", "name": "Sick Leave", "default_annual_days": Decimal("12"), "is_paid": True, "requires_balance": True},
    {"code": "CASUAL", "name": "Casual Leave", "default_annual_days": Decimal("6"), "is_paid": True, "requires_balance": True},
    {"code": "UNPAID", "name": "Unpaid Leave", "default_annual_days": Decimal("0"), "is_paid": False, "requires_balance": False},
]


def seed_default_leave_types(apps, schema_editor):
    """Create the default leave types, leaving any existing ones (matched by code) untouched."""
    LeaveType = apps.get_model("leaves", "LeaveType")
    for leave_type in DEFAULT_LEAVE_TYPES:
        defaults = {key: value for key, value in leave_type.items() if key != "code"}
        LeaveType.objects.get_or_create(code=leave_type["code"], defaults=defaults)


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0001_initial'),
    ]

    operations = [
        # Reversing does not delete leave types: they may have been edited by HR
        # or be referenced by balances and requests.
        migrations.RunPython(seed_default_leave_types, migrations.RunPython.noop),
    ]
