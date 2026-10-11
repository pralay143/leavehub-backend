from django.contrib import admin

from .models import LeaveBalance, LeaveType


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "default_annual_days", "is_paid", "requires_balance", "is_active")
    list_filter = ("is_paid", "requires_balance", "is_active")
    search_fields = ("name", "code")


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "year", "allocated", "used", "remaining")
    list_filter = ("year", "leave_type")
    search_fields = ("employee__email", "employee__first_name", "employee__last_name")
    list_select_related = ("employee", "leave_type")

    @admin.display(description="Remaining (excl. pending)")
    def remaining(self, balance):
        return balance.remaining

    def get_readonly_fields(self, request, obj=None):
        # `used` is maintained by the leave request service. Once a balance
        # exists, its owner, type and year are fixed so used days stay correct.
        if obj is None:
            return ("used",)
        return ("employee", "leave_type", "year", "used")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "leave_type":
            kwargs["queryset"] = LeaveType.objects.filter(requires_balance=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
