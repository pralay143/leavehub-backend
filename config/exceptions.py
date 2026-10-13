from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.fields import get_error_detail
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from leaves.services import LeaveRequestError


def exception_handler(exc, context):
    """DRF exception handler that also maps LeaveHub and Django errors to 400.

    - LeaveRequestError -> 400 {"detail": "<message>"}
    - Django ValidationError (model clean / constraints) -> 400 per-field errors
    """
    if isinstance(exc, LeaveRequestError):
        return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=get_error_detail(exc))
    return drf_exception_handler(exc, context)
