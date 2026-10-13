from rest_framework import serializers

from .models import LeaveType


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = ["id", "name", "code", "default_annual_days", "is_paid", "requires_balance", "is_active"]

    def validate_code(self, code):
        if self.instance is not None and code != self.instance.code:
            raise serializers.ValidationError("The code cannot be changed after the leave type is created.")
        return code
