from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        trim_whitespace=False,
    )

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "role"]  # noqa: RUF012
        read_only_fields = ["id"]  # noqa: RUF012

    def validate(self, attrs):
        password = attrs.get("password")

        if password is not None:
            user = User(
                username=attrs.get(
                    "username",
                    getattr(self.instance, "username", ""),
                ),
                email=attrs.get(
                    "email",
                    getattr(self.instance, "email", ""),
                ),
            )
            validate_password(password, user=user)

        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)

        for field, value in validated_data.items():
            setattr(instance, field, value)

        if password is not None:
            instance.set_password(password)

        instance.save()
        return instance