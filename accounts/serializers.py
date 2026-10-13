import copy

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Department, User, normalize_email


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


class TokenPairSerializer(serializers.Serializer):
    """Response of the login endpoint (documentation only)."""

    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class UserSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name"]

    def get_full_name(self, user) -> str:
        return display_name(user)


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "is_active"]


class MeSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    manager = UserSummarySerializer(read_only=True, allow_null=True)
    department = DepartmentSerializer(read_only=True, allow_null=True)

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

    def get_full_name(self, user) -> str:
        return display_name(user)


class UserSerializer(serializers.ModelSerializer):
    """HR-facing user management.

    Runs the model's own validation (case-insensitive unique email, manager
    rules, role change with direct reports) so the API enforces the same
    rules as the admin.
    """

    email = serializers.EmailField(max_length=254)
    full_name = serializers.SerializerMethodField()
    password = serializers.CharField(
        write_only=True,
        required=False,
        style={"input_type": "password"},
        help_text="Initial password. Required when creating a user; cannot be changed here.",
    )
    manager = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), allow_null=True, required=False
    )
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), allow_null=True, required=False
    )

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
            "is_active",
            "is_superuser",
            "date_joined",
            "last_login",
            "password",
        ]
        read_only_fields = ["is_superuser", "date_joined", "last_login"]

    def get_full_name(self, user) -> str:
        return display_name(user)

    def validate_email(self, value):
        return normalize_email(value)

    def validate_department(self, department):
        current = self.instance.department if self.instance else None
        if department is not None and not department.is_active and department != current:
            raise serializers.ValidationError("This department is inactive.")
        return department

    def validate(self, attrs):
        password = attrs.pop("password", None)
        request = self.context.get("request")

        if self.instance is None and not password:
            raise serializers.ValidationError({"password": "An initial password is required."})
        if self.instance is not None and password is not None:
            raise serializers.ValidationError(
                {"password": "Passwords cannot be changed through this endpoint."}
            )

        if self.instance is not None and request is not None:
            if self.instance.is_superuser and not request.user.is_superuser:
                raise serializers.ValidationError("Only a superuser can change a superuser account.")
            if self.instance.pk == request.user.pk:
                if attrs.get("is_active") is False:
                    raise serializers.ValidationError({"is_active": "You cannot deactivate yourself."})
                if "role" in attrs and attrs["role"] != self.instance.role:
                    raise serializers.ValidationError({"role": "You cannot change your own role."})

        # Validate on a copy so a failed request leaves the instance untouched.
        candidate = copy.copy(self.instance) if self.instance is not None else User()
        for field, value in attrs.items():
            setattr(candidate, field, value)
        candidate.full_clean(exclude=["password"])

        if password is not None:
            try:
                validate_password(password, user=candidate)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": list(exc.messages)})
            attrs["password"] = password
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)
