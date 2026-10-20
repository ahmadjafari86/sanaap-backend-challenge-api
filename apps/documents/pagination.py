from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination


class DocumentPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_page_number(self, request, paginator):
        return self._integer_parameter(request, self.page_query_param, default=1)

    def get_page_size(self, request):
        return self._integer_parameter(
            request,
            self.page_size_query_param,
            default=self.page_size,
            max_value=self.max_page_size,
        )

    @staticmethod
    def _integer_parameter(request, name, *, default, max_value=None):
        field = serializers.IntegerField(min_value=1, max_value=max_value)
        try:
            return field.run_validation(request.query_params.get(name, default))
        except serializers.ValidationError as error:
            raise serializers.ValidationError({name: error.detail}) from error
