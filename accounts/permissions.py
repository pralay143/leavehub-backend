from rest_framework.permissions import SAFE_METHODS, BasePermission


def is_hr(user):
    """HR staff: an active user with the HR role, or a superuser."""
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and (user.is_superuser or user.role == user.Role.HR)
    )


class IsHR(BasePermission):
    message = "Only HR can do this."

    def has_permission(self, request, view):
        return is_hr(request.user)


class IsHROrReadOnly(BasePermission):
    """Any authenticated user may read; only HR may write."""

    message = "Only HR can change this."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return is_hr(request.user)
