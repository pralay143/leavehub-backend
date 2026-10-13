from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.generics import RetrieveAPIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView

from config.viewsets import CreateListRetrieveUpdateViewSet, bool_query_param, int_query_param

from .models import Department, User
from .permissions import IsHR, IsHROrReadOnly, is_hr
from .serializers import (
    DepartmentSerializer,
    LoginSerializer,
    MeSerializer,
    TokenPairSerializer,
    UserSerializer,
)


@extend_schema_view(post=extend_schema(request=LoginSerializer, responses=TokenPairSerializer))
class LoginView(TokenObtainPairView):
    """POST email and password; returns access and refresh tokens."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class MeView(RetrieveAPIView):
    """The logged-in user's profile, role, manager and department."""

    serializer_class = MeSerializer

    def get_object(self):
        return User.objects.select_related("manager", "department").get(pk=self.request.user.pk)


class DepartmentViewSet(CreateListRetrieveUpdateViewSet):
    """Departments. Everyone can read active ones; HR sees all and can create and update."""

    serializer_class = DepartmentSerializer
    permission_classes = [IsHROrReadOnly]
    pagination_class = None

    def get_queryset(self):
        departments = Department.objects.all()
        if not is_hr(self.request.user):
            departments = departments.filter(is_active=True)
        return departments


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("role", OpenApiTypes.STR, enum=[r for r, _ in User.Role.choices]),
            OpenApiParameter("department", OpenApiTypes.INT),
            OpenApiParameter("manager", OpenApiTypes.INT),
            OpenApiParameter("is_active", OpenApiTypes.BOOL),
            OpenApiParameter("search", OpenApiTypes.STR, description="Matches email, username, first or last name."),
        ]
    )
)
class UserViewSet(CreateListRetrieveUpdateViewSet):
    """User management for HR.

    Filters: ?role=, ?department=<id>, ?manager=<id>, ?is_active=true|false,
    ?search=<name or email>.
    """

    serializer_class = UserSerializer
    permission_classes = [IsHR]

    def get_queryset(self):
        users = User.objects.select_related("manager", "department").order_by("email")
        if self.action != "list":
            return users

        request = self.request
        role = request.query_params.get("role")
        if role:
            users = users.filter(role=role.upper())
        department = int_query_param(request, "department")
        if department is not None:
            users = users.filter(department_id=department)
        manager = int_query_param(request, "manager")
        if manager is not None:
            users = users.filter(manager_id=manager)
        is_active = bool_query_param(request, "is_active")
        if is_active is not None:
            users = users.filter(is_active=is_active)
        search = request.query_params.get("search", "").strip()
        if search:
            users = users.filter(
                Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(username__icontains=search)
            )
        return users
