"""Read-only API over the resolved links and the products found in them."""

from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from links.models import LinkDetails, LinkProducts


class LinkDetailSerializer(serializers.ModelSerializer):
    # `content_id` and the original `link` live on the related Links row.
    link_id = serializers.IntegerField(read_only=True)
    content_id = serializers.IntegerField(source="link.content_id", read_only=True)
    link = serializers.CharField(source="link.link", read_only=True)

    class Meta:
        model = LinkDetails
        fields = [
            "id", "link_id", "content_id", "link", "link_detail",
            "brand_exist", "brand_name", "marketplace_brand_id",
            "status", "status_code", "redirect_chain", "resolved_at",
        ]  # fmt: skip


class LinkProductSerializer(serializers.ModelSerializer):
    link_id = serializers.IntegerField(read_only=True)
    content_id = serializers.IntegerField(source="link.content_id", read_only=True)

    class Meta:
        model = LinkProducts
        fields = [
            "id", "link_id", "content_id", "platform", "product_id", "source",
            "product_name", "brand", "status", "fetched_at",
        ]  # fmt: skip


class PageCountPagination(PageNumberPagination):
    """Fixed 1000 rows per page, plus how many pages exist and how many are left."""

    page_size = 1000

    def get_paginated_response(self, data):
        total_pages = self.page.paginator.num_pages
        current_page = self.page.number
        return Response(
            {
                "count": self.page.paginator.count,
                "total_pages": total_pages,
                "current_page": current_page,
                "remaining_pages": total_pages - current_page,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )


class LinkDetailListView(ListAPIView):
    """GET /api/link-details/?page=N  (admin only, see REST_FRAMEWORK in settings)."""

    # select_related joins Links in the same query: no extra query per row (N+1).
    # A fixed order keeps pages stable between requests.
    queryset = LinkDetails.objects.select_related("link").order_by("id")
    serializer_class = LinkDetailSerializer
    pagination_class = PageCountPagination


class LinkProductListView(ListAPIView):
    """GET /api/link-products/?page=N[&platform=trendyol]  (admin only)."""

    serializer_class = LinkProductSerializer
    pagination_class = PageCountPagination

    def get_queryset(self):
        products = LinkProducts.objects.select_related("link").order_by("id")
        platform = self.request.query_params.get("platform")
        return products.filter(platform=platform) if platform else products
