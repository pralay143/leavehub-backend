from rest_framework.generics import RetrieveAPIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import MeSerializer


class LoginView(TokenObtainPairView):
    """POST email and password; returns access and refresh tokens."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class MeView(RetrieveAPIView):
    """The logged-in user's profile, role, manager and department."""

    serializer_class = MeSerializer

    def get_object(self):
        return User.objects.select_related("manager", "department").get(pk=self.request.user.pk)
