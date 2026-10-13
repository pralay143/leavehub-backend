from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializers import UserSummarySerializer

from .models import LeaveBalance, LeaveRequest, LeaveType
from .services import can_review, pending_days

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


class LeaveRequestSerializer(serializers.ModelSerializer):
    """A leave request, with flags telling the current user which actions apply."""

    employee = UserSummarySerializer(read_only=True)
    leave_type = LeaveTypeSummarySerializer(read_only=True)
    reviewed_by = UserSummarySerializer(read_only=True)
    can_approve = serializers.SerializerMethodField()
    can_reject = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee",
            "leave_type",
            "start_date",
            "end_date",
            "half_day",
            "days",
            "reason",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_comment",
            "created_at",
            "updated_at",
            "can_approve",
            "can_reject",
            "can_cancel",
        ]
        read_only_fields = fields

    def _user(self):
        request = self.context.get("request")
        return request.user if request is not None else None

    def get_can_approve(self, leave_request) -> bool:
        user = self._user()
        return bool(
            user
            and leave_request.status == LeaveRequest.Status.PENDING
            and can_review(user, leave_request)
        )

    def get_can_reject(self, leave_request) -> bool:
        return self.get_can_approve(leave_request)

    def get_can_cancel(self, leave_request) -> bool:
        user = self._user()
        if user is None or user.pk != leave_request.employee_id:
            return False
        if leave_request.status == LeaveRequest.Status.PENDING:
            return True
        return (
            leave_request.status == LeaveRequest.Status.APPROVED
            and leave_request.start_date > timezone.localdate()
        )


class LeaveRequestCreateSerializer(serializers.Serializer):
    """Input for a new request. The employee is the logged-in user and the
    number of days is calculated by the server."""

    leave_type = serializers.PrimaryKeyRelatedField(queryset=LeaveType.objects.all())
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    half_day = serializers.BooleanField(default=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)


class ReviewSerializer(serializers.Serializer):
    comment = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=2000,
        help_text="Optional when approving; required when rejecting.",
    )
