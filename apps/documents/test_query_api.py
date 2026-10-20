from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Document

User = get_user_model()


class DocumentQueryAPITests(APITestCase):
    url = "/api/v1/documents/"

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="query-owner-one")
        cls.other_owner = User.objects.create_user(username="query-owner-two")
        cls.readers = {
            role: User.objects.create_user(username=f"query-{role}", role=role)
            for role in User.Role.values
        }
        cls.first = cls.make_document("Quarterly Report", cls.owner, "first")
        cls.second = cls.make_document("quarterly report draft", cls.other_owner, "second")
        cls.third = cls.make_document("Invoice", cls.owner, "third")
        cls.fourth = cls.make_document("Quarterly Summary", cls.other_owner, "fourth")
        cls.fifth = cls.make_document("Unrelated", cls.owner, "fifth")
        cls.sixth = cls.make_document("گزارش ماهانه", cls.other_owner, "sixth")
        cls.fifth.original_filename = "quarterly-report.png"
        cls.fifth.save(update_fields=["original_filename"])
        Document.objects.update(created_at=datetime(2026, 1, 1, tzinfo=UTC))

    @staticmethod
    def make_document(title, creator, key):
        return Document.objects.create(
            title=title,
            original_filename=f"{key}.png",
            object_key=f"documents/{key}.png",
            content_type="image/png",
            file_size=128,
            creator=creator,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.readers[User.Role.VIEWER])

    def add_extra_documents(self, count):
        return Document.objects.bulk_create([
            Document(
                title=f"Extra image {index}",
                original_filename=f"extra-{index}.png",
                object_key=f"documents/extra-{index}.png",
                content_type="image/png",
                file_size=128,
                creator=self.owner,
            )
            for index in range(count)
        ])

    def assert_page(self, response, *, count, ids):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, dict)
        self.assertEqual(set(response.data), {"count", "next", "previous", "results"})
        self.assertEqual(response.data["count"], count)
        self.assertEqual([item["id"] for item in response.data["results"]], ids)

    def test_default_page_limits_results_to_ten_and_counts_all_matches(self):
        extras = self.add_extra_documents(6)

        response = self.client.get(self.url)

        self.assert_page(
            response,
            count=12,
            ids=[document.pk for document in reversed(extras)] + [
                self.sixth.pk, self.fifth.pk, self.fourth.pk, self.third.pk,
            ],
        )
        self.assertIsNone(response.data["previous"])
        self.assertEqual(parse_qs(urlsplit(response.data["next"]).query)["page"], ["2"])

    def test_pages_preserve_descending_id_order_for_equal_creation_times(self):
        first = self.client.get(self.url, {"page_size": 2})
        self.assert_page(first, count=6, ids=[self.sixth.pk, self.fifth.pk])
        self.assertIsNone(first.data["previous"])

        second = self.client.get(first.data["next"])
        self.assert_page(second, count=6, ids=[self.fourth.pk, self.third.pk])
        self.assertIsNotNone(second.data["previous"])

        third = self.client.get(second.data["next"])
        self.assert_page(third, count=6, ids=[self.second.pk, self.first.pk])
        self.assertIsNone(third.data["next"])
        previous = self.client.get(third.data["previous"])
        self.assert_page(previous, count=6, ids=[self.fourth.pk, self.third.pk])

    def test_page_size_can_reach_one_hundred_but_still_bounds_results(self):
        extras = self.add_extra_documents(100)
        Document.objects.filter(pk__in=[item.pk for item in extras]).update(
            created_at=datetime(2026, 2, 1, tzinfo=UTC),
        )

        response = self.client.get(self.url, {"page_size": 100})

        self.assert_page(response, count=106, ids=[item.pk for item in reversed(extras)])
        self.assertIsNotNone(response.data["next"])

    def test_invalid_pagination_parameters_return_field_errors(self):
        cases = {
            "page": ("", "0", "-1", "1.5", "many", "last"),
            "page_size": ("", "0", "-1", "1.5", "many", "101", "999999"),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    response = self.client.get(self.url, {field: value})

                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertIn(field, response.data)

    def test_valid_page_beyond_result_set_returns_not_found(self):
        response = self.client.get(self.url, {"page": 4, "page_size": 2})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_search_matches_a_case_insensitive_title_substring(self):
        response = self.client.get(self.url, {"search": "REPORT"})

        self.assert_page(response, count=2, ids=[self.second.pk, self.first.pk])
        partial = self.client.get(self.url, {"search": "uarter"})
        self.assert_page(partial, count=3, ids=[self.fourth.pk, self.second.pk, self.first.pk])

    def test_search_trims_whitespace_and_preserves_the_literal_phrase(self):
        response = self.client.get(self.url, {"search": "  Quarterly Report  "})

        self.assert_page(response, count=2, ids=[self.second.pk, self.first.pk])
        reversed_phrase = self.client.get(self.url, {"search": "Report Quarterly"})
        self.assert_page(reversed_phrase, count=0, ids=[])

    def test_search_does_not_match_filenames_or_creator_usernames(self):
        for search in ("quarterly-report", "query-owner-one"):
            with self.subTest(search=search):
                response = self.client.get(self.url, {"search": search})

                self.assert_page(response, count=0, ids=[])

    def test_blank_search_leaves_the_shared_list_unfiltered(self):
        response = self.client.get(self.url, {"search": "  "})

        self.assert_page(response, count=6, ids=[
            self.sixth.pk, self.fifth.pk, self.fourth.pk,
            self.third.pk, self.second.pk, self.first.pk,
        ])

    def test_search_accepts_persian_titles(self):
        response = self.client.get(self.url, {"search": "گزارش"})

        self.assert_page(response, count=1, ids=[self.sixth.pk])

    def test_search_treats_sql_wildcards_as_literal_title_characters(self):
        document = self.make_document("Budget 100%_done", self.owner, "literal-search")

        response = self.client.get(self.url, {"search": "%_"})

        self.assert_page(response, count=1, ids=[document.pk])

    def test_search_length_boundary_is_validated(self):
        document = self.make_document("x" * 255, self.owner, "long-title")
        valid = self.client.get(self.url, {"search": "x" * 255})
        self.assert_page(valid, count=1, ids=[document.pk])

        invalid = self.client.get(self.url, {"search": "x" * 256})

        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("search", invalid.data)

    def test_creator_filter_keeps_other_creators_out_of_results(self):
        response = self.client.get(self.url, {"creator": self.owner.pk})

        self.assert_page(response, count=3, ids=[self.fifth.pk, self.third.pk, self.first.pk])

    def test_combined_filters_run_before_pagination_and_survive_page_links(self):
        first = self.client.get(self.url, {
            "search": "quarterly", "creator": self.other_owner.pk, "page_size": 1,
        })
        self.assert_page(first, count=2, ids=[self.fourth.pk])
        next_query = parse_qs(urlsplit(first.data["next"]).query)
        self.assertEqual(next_query["search"], ["quarterly"])
        self.assertEqual(next_query["creator"], [str(self.other_owner.pk)])
        self.assertEqual(next_query["page_size"], ["1"])

        second = self.client.get(first.data["next"])

        self.assert_page(second, count=2, ids=[self.second.pk])
        self.assertIsNone(second.data["next"])
        previous = self.client.get(second.data["previous"])
        self.assert_page(previous, count=2, ids=[self.fourth.pk])

    def test_unknown_creator_returns_an_empty_first_page(self):
        response = self.client.get(self.url, {"creator": 999999})

        self.assert_page(response, count=0, ids=[])
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        beyond_first = self.client.get(self.url, {"creator": 999999, "page": 2})
        self.assertEqual(beyond_first.status_code, status.HTTP_404_NOT_FOUND)

    def test_invalid_creator_filter_returns_field_errors(self):
        for value in ("", "0", "-1", "1.5", "someone", "9223372036854775808", "1 OR 1=1"):
            with self.subTest(value=value):
                response = self.client.get(self.url, {"creator": value})

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("creator", response.data)

    def test_all_roles_can_filter_and_paginate_other_users_documents(self):
        for role, reader in self.readers.items():
            with self.subTest(role=role):
                self.client.force_authenticate(user=reader)
                response = self.client.get(self.url, {
                    "creator": self.owner.pk, "page_size": 1,
                })

                self.assert_page(response, count=3, ids=[self.fifth.pk])

    def test_anonymous_requests_are_rejected_before_query_validation(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.url, {"creator": "invalid", "page": -1})

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_role_is_rejected_before_query_validation(self):
        reader = self.readers[User.Role.VIEWER]
        reader.role = "unknown"
        self.client.force_authenticate(user=reader)

        response = self.client.get(self.url, {"creator": "invalid", "page": -1})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_parameters_do_not_filter_detail_requests(self):
        response = self.client.get(f"{self.url}{self.first.pk}/", {
            "search": "different", "creator": "invalid", "page_size": -1,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.first.pk)
