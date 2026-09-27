"""One page-number pagination for every list endpoint (BUILD-SEC section 6)."""
from rest_framework.pagination import PageNumberPagination


class VerdictPagination(PageNumberPagination):
    """50 rows a page, 100 at most so one request cannot ask for the whole table."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100
