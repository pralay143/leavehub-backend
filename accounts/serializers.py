from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Department, User


def display_name(user):
    return user.get_full_name() or user.email


class LoginSerializer(TokenObtainPairSerializer):
    """Email/password login that adds display claims to the tokens.

    The extra claims are for the UI only; permissions are always checked
    against the user record in the database, never against token claims.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["full_name"] = display_name(user)
        return token


class UserSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name"]

    def get_full_name(self, user):
        return display_name(user)


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name"]


class MeSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    manager = UserSummarySerializer(read_only=True)
    department = DepartmentSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "manager",
            "department",
            "is_superuser",
        ]
        read_only_fields = fields

    def get_full_name(self, user):
        return display_name(user)
