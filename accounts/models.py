from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model for LeaveHub.

    Defined before the first migration so that leave-related fields
    (role, manager, department, ...) can be added later as ordinary
    migrations instead of requiring a user model swap.
    """
