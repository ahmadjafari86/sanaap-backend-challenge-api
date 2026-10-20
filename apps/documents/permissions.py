from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.accounts.models import User


class DocumentRolePermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        role = request.user.role

        if role not in User.Role.values:
            return False

        if request.method in SAFE_METHODS:
            return True

        if request.method in ("POST", "PUT", "PATCH"):
            return role in (User.Role.ADMIN, User.Role.EDITOR)

        if request.method == "DELETE":
            return role == User.Role.ADMIN

        return False
    