from decimal import Decimal

from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsHR, IsHROrReadOnly, is_hr
from config.viewsets import CreateListRetrieveUpdateViewSet, int_query_param

from .models import LeaveBalance, LeaveRequest, LeaveType, PublicHoliday
from .serializers import (
    AllocateBalancesSerializer,
    AllocationResultSerializer,
    LeaveBalanceSerializer,
    LeaveRequestCreateSerializer,
    LeaveRequestSerializer,
    LeaveTypeSerializer,
    PublicHolidaySerializer,
    ReviewSerializer,
)
from .services import (
    allocate_leave_balances,
    approve_leave_request,
    cancel_leave_request,
    create_leave_request,
    reject_leave_request,
    review_queue_filter,
)


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


SCOPES = ("mine", "team", "review", "all")


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "scope",
                OpenApiTypes.STR,
                enum=SCOPES,
                description=(
                    "mine (default): your requests. team: your direct reports. "
                    "review: pending requests routed to you. all: HR only."
                ),
            ),
            OpenApiParameter("status", OpenApiTypes.STR, enum=[s for s, _ in LeaveRequest.Status.choices]),
            OpenApiParameter("year", OpenApiTypes.INT, description="Year of the start date."),
            OpenApiParameter("leave_type", OpenApiTypes.INT),
        ]
    ),
    create=extend_schema(request=LeaveRequestCreateSerializer, responses={201: LeaveRequestSerializer}),
)
class LeaveRequestViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Leave requests.

    You can see your own requests, your direct reports' requests and, as HR,
    everyone's. Requests are never edited or deleted: cancel and resubmit.
    """

    serializer_class = LeaveRequestSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation
            return LeaveRequest.objects.none()
        user = self.request.user
        requests = LeaveRequest.objects.select_related(
            "employee", "employee__manager", "leave_type", "reviewed_by"
        ).order_by("-created_at")
        if not is_hr(user):
            requests = requests.filter(Q(employee=user) | Q(employee__manager=user))

        if self.action == "list":
            requests = self._apply_list_filters(requests, user)
        return requests

    def _apply_list_filters(self, requests, user):
        params = self.request.query_params
        scope = params.get("scope", "mine")
        if scope not in SCOPES:
            raise ValidationError({"scope": f"Must be one of: {', '.join(SCOPES)}."})
        if scope == "mine":
            requests = requests.filter(employee=user)
        elif scope == "team":
            requests = requests.filter(employee__manager=user)
        elif scope == "review":
            requests = requests.filter(review_queue_filter(user), status=LeaveRequest.Status.PENDING)
        elif not is_hr(user):
            raise PermissionDenied("Only HR can list all leave requests.")

        status_value = params.get("status")
        if status_value:
            status_value = status_value.upper()
            if status_value not in LeaveRequest.Status.values:
                raise ValidationError({"status": f"Must be one of: {', '.join(LeaveRequest.Status.values)}."})
            requests = requests.filter(status=status_value)
        year = int_query_param(self.request, "year")
        if year is not None:
            requests = requests.filter(start_date__year=year)
        leave_type = int_query_param(self.request, "leave_type")
        if leave_type is not None:
            requests = requests.filter(leave_type_id=leave_type)
        return requests

    def _respond(self, leave_request, status_code=status.HTTP_200_OK):
        leave_request = self.get_queryset().get(pk=leave_request.pk)
        return Response(self.get_serializer(leave_request).data, status=status_code)

    def create(self, request, *args, **kwargs):
        params = LeaveRequestCreateSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        leave_request = create_leave_request(employee=request.user, **params.validated_data)
        return self._respond(leave_request, status.HTTP_201_CREATED)

    @extend_schema(request=ReviewSerializer, responses=LeaveRequestSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        params = ReviewSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        leave_request = approve_leave_request(
            leave_request=self.get_object(), reviewer=request.user, comment=params.validated_data["comment"]
        )
        return self._respond(leave_request)

    @extend_schema(request=ReviewSerializer, responses=LeaveRequestSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        params = ReviewSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        leave_request = reject_leave_request(
            leave_request=self.get_object(), reviewer=request.user, comment=params.validated_data["comment"]
        )
        return self._respond(leave_request)

    @extend_schema(request=None, responses=LeaveRequestSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        leave_request = cancel_leave_request(leave_request=self.get_object(), user=request.user)
        return self._respond(leave_request)


@extend_schema_view(
    list=extend_schema(
        parameters=[OpenApiParameter("year", OpenApiTypes.INT, description="Defaults to the current year.")]
    )
)
class PublicHolidayViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Company-wide public holidays, which are not counted as leave days.

    Everyone can read them; HR can create, update and delete them. Changing
    holidays does not recalculate requests that were already submitted.
    """

    serializer_class = PublicHolidaySerializer
    permission_classes = [IsHROrReadOnly]
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        holidays = PublicHoliday.objects.all()
        if self.action == "list":
            year = int_query_param(self.request, "year") or timezone.localdate().year
            holidays = holidays.filter(date__year=year)
        return holidays
