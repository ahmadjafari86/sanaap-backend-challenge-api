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

    def get_schema_operation_parameters(self, view):
        parameters = super().get_schema_operation_parameters(view)
        for parameter in parameters:
            parameter["schema"]["minimum"] = 1
            if parameter["name"] == self.page_query_param:
                parameter["schema"]["default"] = 1
                parameter["description"] = "Positive page number. A valid page beyond the results returns 404."
            elif parameter["name"] == self.page_size_query_param:
                parameter["schema"].update(default=self.page_size, maximum=self.max_page_size)
                parameter["description"] = "Results per page. Invalid values or sizes above the maximum return 400."
        return parameters

    @staticmethod
    def _integer_parameter(request, name, *, default, max_value=None):
        field = serializers.IntegerField(min_value=1, max_value=max_value)
        try:
            return field.run_validation(request.query_params.get(name, default))
        except serializers.ValidationError as error:
            raise serializers.ValidationError({name: error.detail}) from error
