from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializers import UserSummarySerializer

from .models import LeaveBalance, LeaveType
from .services import pending_days

DAYS_FIELD = serializers.DecimalField(max_digits=6, decimal_places=1)


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = ["id", "name", "code", "default_annual_days", "is_paid", "requires_balance", "is_active"]

    def validate_code(self, code):
        if self.instance is not None and code != self.instance.code:
            raise serializers.ValidationError("The code cannot be changed after the leave type is created.")
        return code


class LeaveTypeSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = ["id", "name", "code"]


class LeaveBalanceSerializer(serializers.ModelSerializer):
    """A balance with pending and available days. Only `allocated` is writable (HR)."""

    employee = UserSummarySerializer(read_only=True)
    leave_type = LeaveTypeSummarySerializer(read_only=True)
    pending = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()

    class Meta:
        model = LeaveBalance
        fields = ["id", "employee", "leave_type", "year", "allocated", "used", "pending", "available"]
        read_only_fields = ["id", "year", "used"]

    def _pending(self, balance):
        # Annotated by the list/detail queryset; calculated directly otherwise.
        pending = getattr(balance, "pending", None)
        if pending is None:
            pending = pending_days(balance.employee_id, balance.leave_type_id, balance.year)
        return pending

    @extend_schema_field(DAYS_FIELD)
    def get_pending(self, balance):
        return DAYS_FIELD.to_representation(self._pending(balance))

    @extend_schema_field(DAYS_FIELD)
    def get_available(self, balance):
        return DAYS_FIELD.to_representation(balance.allocated - balance.used - self._pending(balance))

    def update(self, balance, validated_data):
        allocated = validated_data.get("allocated", balance.allocated)
        # Lock the row so a concurrent approval cannot raise `used` above the
        # new allocation between the check and the save.
        with transaction.atomic():
            locked = LeaveBalance.objects.select_for_update().get(pk=balance.pk)
            if allocated < locked.used:
                raise serializers.ValidationError(
                    {"allocated": f"Allocated days cannot be less than days already used ({locked.used})."}
                )
            locked.allocated = allocated
            locked.save(update_fields=["allocated"])
        return LeaveBalance.objects.select_related("employee", "leave_type").get(pk=locked.pk)


class AllocateBalancesSerializer(serializers.Serializer):
    year = serializers.IntegerField(min_value=2000, max_value=2100)
    dry_run = serializers.BooleanField(default=False)


class AllocationResultSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    dry_run = serializers.BooleanField()
    created = serializers.IntegerField(help_text="Balances created (0 for a dry run).")
    to_create = serializers.IntegerField(help_text="Balances that were missing.")
    already_existed = serializers.IntegerField()
    active_users = serializers.IntegerField()
    leave_types = serializers.IntegerField()
