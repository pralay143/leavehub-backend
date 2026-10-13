from decimal import Decimal

from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsHR, IsHROrReadOnly, is_hr
from config.viewsets import CreateListRetrieveUpdateViewSet, int_query_param

from .models import LeaveBalance, LeaveRequest, LeaveType
from .serializers import (
    AllocateBalancesSerializer,
    AllocationResultSerializer,
    LeaveBalanceSerializer,
    LeaveTypeSerializer,
)
from .services import allocate_leave_balances


class LeaveTypeViewSet(CreateListRetrieveUpdateViewSet):
    """Leave types. Everyone can read active ones; HR sees all and can create and update.
    The code cannot change after creation."""

    serializer_class = LeaveTypeSerializer
    permission_classes = [IsHROrReadOnly]
    pagination_class = None

    def get_queryset(self):
        leave_types = LeaveType.objects.all()
        if not is_hr(self.request.user):
            leave_types = leave_types.filter(is_active=True)
        return leave_types


def balances_with_pending():
    """Balances annotated with the days in matching pending requests."""
    pending = (
        LeaveRequest.objects.filter(
            employee=OuterRef("employee"),
            leave_type=OuterRef("leave_type"),
            status=LeaveRequest.Status.PENDING,
            start_date__year=OuterRef("year"),
        )
        .values("employee")
        .annotate(total=Sum("days"))
        .values("total")
    )
    days = DecimalField(max_digits=6, decimal_places=1)
    return LeaveBalance.objects.select_related("employee", "leave_type").annotate(
        pending=Coalesce(Subquery(pending, output_field=days), Value(Decimal("0")), output_field=days)
    )


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("year", OpenApiTypes.INT, description="Defaults to the current year."),
            OpenApiParameter("employee", OpenApiTypes.INT, description="HR only: filter by employee id."),
            OpenApiParameter("leave_type", OpenApiTypes.INT),
        ]
    )
)
class LeaveBalanceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Leave balances with pending and available days.

    Employees see their own balances. HR sees all balances, can change
    `allocated` (never below `used`) and can allocate a year of balances.
    """

    serializer_class = LeaveBalanceSerializer
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ("partial_update", "update", "allocate"):
            return [IsHR()]
        return [IsAuthenticated()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation
            return LeaveBalance.objects.none()
        request = self.request
        balances = balances_with_pending().order_by("-year", "employee__email", "leave_type__name")
        if not is_hr(request.user):
            balances = balances.filter(employee=request.user)

        if self.action == "list":
            year = int_query_param(request, "year") or timezone.localdate().year
            balances = balances.filter(year=year)
            employee = int_query_param(request, "employee")
            if employee is not None and is_hr(request.user):
                balances = balances.filter(employee_id=employee)
            leave_type = int_query_param(request, "leave_type")
            if leave_type is not None:
                balances = balances.filter(leave_type_id=leave_type)
        return balances

    @extend_schema(request=AllocateBalancesSerializer, responses=AllocationResultSerializer)
    @action(detail=False, methods=["post"])
    def allocate(self, request):
        """Create missing balances for a year from each leave type's default days."""
        params = AllocateBalancesSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        result = allocate_leave_balances(
            params.validated_data["year"], dry_run=params.validated_data["dry_run"]
        )
        return Response(
            AllocationResultSerializer(
                {
                    "year": result.year,
                    "dry_run": result.dry_run,
                    "created": result.created,
                    "to_create": len(result.balances),
                    "already_existed": result.already_existed,
                    "active_users": result.active_users,
                    "leave_types": result.leave_types,
                }
            ).data
        )
