import json

from drf_spectacular.validation import validate_schema
from rest_framework import status
from rest_framework.test import APISimpleTestCase


class APISchemaTests(APISimpleTestCase):
    schema_url = "/api/schema/"
    docs_url = "/api/docs/"
    metadata_fields = {
        "id", "title", "original_filename", "content_type", "file_size", "status",
        "creator", "created_at", "updated_at",
    }

    def get_schema(self):
        response = self.client.get(self.schema_url, HTTP_ACCEPT="application/vnd.oai.openapi+json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def resolve(self, schema, value):
        while "$ref" in value:
            name = value["$ref"].split("/")[-1]
            value = schema["components"]["schemas"][name]
        return value

    def response_schema(self, schema, operation, code):
        response = operation["responses"][str(code)]
        return self.resolve(schema, response["content"]["application/json"]["schema"])

    def request_schema(self, schema, operation, media_type):
        return self.resolve(schema, operation["requestBody"]["content"][media_type]["schema"])

    def test_schema_is_public_even_with_an_invalid_authorization_header(self):
        for token in (None, "invalid-token"):
            self.client.credentials(**({"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}))
            schema = self.get_schema()
            self.assertTrue(schema["openapi"].startswith("3."))
            self.assertIn("title", schema["info"])

    def test_swagger_is_public_uses_the_schema_route_and_does_not_persist_tokens(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

        response = self.client.get(self.docs_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertEqual(response.data["schema_url"], self.schema_url)
        self.assertIs(json.loads(response.data["settings"])["persistAuthorization"], False)

    def test_schema_publishes_all_business_paths_and_supported_methods(self):
        schema = self.get_schema()
        expected = {
            "/api/v1/auth/token/": {"post"},
            "/api/v1/auth/token/refresh/": {"post"},
            "/api/v1/auth/logout/": {"post"},
            "/api/v1/users/": {"get", "post"},
            "/api/v1/users/{id}/": {"patch"},
            "/api/v1/documents/": {"get", "post"},
            "/api/v1/documents/{id}/": {"get", "put", "patch", "delete"},
            "/api/v1/documents/{id}/download/": {"get"},
        }
        self.assertEqual(set(schema["paths"]), set(expected))
        for path, methods in expected.items():
            with self.subTest(path=path):
                self.assertEqual(set(schema["paths"][path]), methods)

    def test_schema_requires_bearer_jwt_for_business_operations_only(self):
        schema = self.get_schema()
        scheme = schema["components"]["securitySchemes"]["jwtAuth"]
        self.assertEqual(scheme["type"], "http")
        self.assertEqual(scheme["scheme"], "bearer")
        self.assertEqual(scheme["bearerFormat"], "JWT")
        for path, operations in schema["paths"].items():
            for operation in operations.values():
                if "/auth/" in path:
                    self.assertFalse(operation.get("security"))
                else:
                    self.assertEqual(operation["security"], [{"jwtAuth": []}])

    def test_upload_documents_binary_multipart_input_and_metadata_response(self):
        schema = self.get_schema()
        operation = schema["paths"]["/api/v1/documents/"]["post"]
        self.assertEqual(set(operation["requestBody"]["content"]), {"multipart/form-data"})
        request = self.request_schema(schema, operation, "multipart/form-data")
        self.assertEqual(set(request["required"]), {"title", "file"})
        self.assertEqual(request["properties"]["file"]["format"], "binary")
        self.assertEqual(request["properties"]["title"]["maxLength"], 255)
        response = self.response_schema(schema, operation, 202)
        self.assertEqual(set(response["properties"]), self.metadata_fields)

    def test_updates_describe_json_titles_and_optional_multipart_images(self):
        schema = self.get_schema()
        operations = schema["paths"]["/api/v1/documents/{id}/"]
        for method in ("put", "patch"):
            operation = operations[method]
            self.assertEqual(set(operation["requestBody"]["content"]), {
                "application/json", "application/x-www-form-urlencoded", "multipart/form-data",
            })
            json_request = self.request_schema(schema, operation, "application/json")
            multipart = self.request_schema(schema, operation, "multipart/form-data")
            self.assertEqual(set(json_request["properties"]), {"title"})
            self.assertEqual(multipart["properties"]["file"]["format"], "binary")
            self.assertNotIn("file", multipart.get("required", []))
            self.assertEqual("title" in json_request.get("required", []), method == "put")
            response = self.response_schema(schema, operation, 200)
            self.assertEqual(set(response["properties"]), self.metadata_fields)

    def test_list_describes_query_validation_and_the_paginated_envelope(self):
        schema = self.get_schema()
        operation = schema["paths"]["/api/v1/documents/"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}
        self.assertEqual(set(parameters), {"search", "creator", "page", "page_size"})
        self.assertEqual(parameters["search"]["schema"]["maxLength"], 255)
        self.assertEqual(parameters["creator"]["schema"]["minimum"], 1)
        self.assertEqual(parameters["creator"]["schema"]["maximum"], 9223372036854775807)
        self.assertEqual(parameters["page"]["schema"]["minimum"], 1)
        self.assertEqual(parameters["page"]["schema"]["default"], 1)
        self.assertEqual(parameters["page_size"]["schema"]["minimum"], 1)
        self.assertEqual(parameters["page_size"]["schema"]["maximum"], 100)
        self.assertEqual(parameters["page_size"]["schema"]["default"], 10)
        page = self.response_schema(schema, operation, 200)
        self.assertEqual(set(page["properties"]), {"count", "next", "previous", "results"})
        item = self.resolve(schema, page["properties"]["results"]["items"])
        self.assertEqual(set(item["properties"]), self.metadata_fields)

    def test_metadata_responses_hide_storage_keys_versions_and_urls(self):
        schema = self.get_schema()
        operation = schema["paths"]["/api/v1/documents/{id}/"]["get"]
        response = self.response_schema(schema, operation, 200)
        self.assertEqual(set(response["properties"]), self.metadata_fields)
        for field in response["properties"].values():
            self.assertTrue(field["readOnly"])

    def test_download_schema_is_a_single_url_with_a_fixed_sixty_second_lifetime(self):
        schema = self.get_schema()
        operation = schema["paths"]["/api/v1/documents/{id}/download/"]["get"]
        response = self.response_schema(schema, operation, 200)
        self.assertEqual(set(response["properties"]), {"download_url", "expires_in"})
        self.assertEqual(response["properties"]["download_url"]["format"], "uri")
        self.assertEqual(response["properties"]["expires_in"]["minimum"], 60)
        self.assertEqual(response["properties"]["expires_in"]["maximum"], 60)
        self.assertNotIn("requestBody", operation)
        self.assertNotIn("page", {item["name"] for item in operation.get("parameters", [])})

    def test_deletion_documents_an_empty_no_content_response(self):
        schema = self.get_schema()
        operation = schema["paths"]["/api/v1/documents/{id}/"]["delete"]
        self.assertNotIn("content", operation["responses"]["204"])

    def test_user_password_is_required_only_for_creation_and_never_returned(self):
        schema = self.get_schema()
        create = schema["paths"]["/api/v1/users/"]["post"]
        request = self.request_schema(schema, create, "application/json")
        self.assertIn("password", request["required"])
        self.assertTrue(request["properties"]["password"]["writeOnly"])
        response = self.response_schema(schema, create, 201)
        self.assertNotIn("password", response["properties"])
        patch = schema["paths"]["/api/v1/users/{id}/"]["patch"]
        patch_request = self.request_schema(schema, patch, "application/json")
        self.assertNotIn("password", patch_request.get("required", []))

    def test_authentication_schema_matches_token_and_logout_payloads(self):
        schema = self.get_schema()
        login = schema["paths"]["/api/v1/auth/token/"]["post"]
        login_request = self.request_schema(schema, login, "application/json")
        self.assertEqual(set(login_request["required"]), {"username", "password"})
        self.assertEqual(set(self.response_schema(schema, login, 200)["properties"]), {"access", "refresh"})
        refresh = schema["paths"]["/api/v1/auth/token/refresh/"]["post"]
        self.assertEqual(set(self.response_schema(schema, refresh, 200)["properties"]), {"access"})
        logout = schema["paths"]["/api/v1/auth/logout/"]["post"]
        request = self.request_schema(schema, logout, "application/json")
        self.assertEqual(set(request["required"]), {"refresh"})
        empty = self.response_schema(schema, logout, 200)
        self.assertEqual(empty["type"], "object")
        self.assertIs(empty["additionalProperties"], False)

    def test_document_and_user_error_statuses_are_published(self):
        schema = self.get_schema()
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                codes = set(operation["responses"])
                self.assertIn("401", codes)
                if "/auth/" not in path:
                    self.assertIn("403", codes)
                if "{id}" in path:
                    self.assertIn("404", codes)
                if method in ("post", "put", "patch"):
                    self.assertIn("400", codes)
                if "/documents/" in path and (method in ("post", "put", "patch", "delete") or "download" in path):
                    self.assertIn("503", codes)
        self.assertIn("400", schema["paths"]["/api/v1/documents/"]["get"]["responses"])
        self.assertIn("404", schema["paths"]["/api/v1/documents/"]["get"]["responses"])

    def test_published_schema_is_valid_openapi(self):
        validate_schema(self.get_schema())
