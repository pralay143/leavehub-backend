import logging

from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)

HealthSerializer = inline_serializer("Health", fields={"status": serializers.CharField()})


@extend_schema(responses={200: HealthSerializer, 503: HealthSerializer})
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def health(request):
    """Liveness and database check for load balancers and hosting platforms."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.exception("Health check: database unavailable")
        return Response({"status": "database unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"status": "ok"})
