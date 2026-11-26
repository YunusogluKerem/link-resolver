from django.contrib import admin
from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from links.api import LinkDetailListView, LinkProductListView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/token/", TokenObtainPairView.as_view(), name="token"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("api/link-details/", LinkDetailListView.as_view(), name="link-details"),
    path("api/link-products/", LinkProductListView.as_view(), name="link-products"),
]
