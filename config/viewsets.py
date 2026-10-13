from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError


class CreateListRetrieveUpdateViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve, create and partially update. No PUT and no DELETE:
    records are deactivated rather than deleted."""

    http_method_names = ["get", "post", "patch", "head", "options"]


def int_query_param(request, name):
    """An optional integer query parameter; 400 if present but not an integer."""
    value = request.query_params.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        raise ValidationError({name: "Must be an integer."})


def bool_query_param(request, name):
    """An optional true/false query parameter; 400 if present but invalid."""
    value = request.query_params.get(name)
    if value in (None, ""):
        return None
    if value.lower() in ("true", "1"):
        return True
    if value.lower() in ("false", "0"):
        return False
    raise ValidationError({name: "Must be true or false."})
