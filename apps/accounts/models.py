from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Application user with Django's username and password authentication."""