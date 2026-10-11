from django.contrib import admin

from .models import LeaveType


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "default_annual_days", "is_paid", "requires_balance", "is_active")
    list_filter = ("is_paid", "requires_balance", "is_active")
    search_fields = ("name", "code")
