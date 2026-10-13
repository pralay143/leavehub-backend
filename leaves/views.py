from accounts.permissions import IsHROrReadOnly, is_hr
from config.viewsets import CreateListRetrieveUpdateViewSet

from .models import LeaveType
from .serializers import LeaveTypeSerializer


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
