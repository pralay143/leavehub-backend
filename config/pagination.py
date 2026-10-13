from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """20 results per page by default; clients may ask for up to 100 with ?page_size=."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
