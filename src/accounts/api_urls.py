"""Users, API tokens and authentication.

JSON API routes under /api/v1/. No trailing slashes: the acceptance checker
posts straight at these paths and a redirect would turn its POST into a GET
(BUILD-SEC section 16).
"""
from django.urls import path

from accounts.api import (
    AdminUserDetailView,
    AdminUserListView,
    ChangePasswordView,
    DemoLoginView,
    LoginView,
    LogoutView,
    MeView,
    RegisterView,
    ResetLinkView,
    ResetPasswordView,
    TokenDetailView,
    TokenListCreateView,
)

urlpatterns = [
    path("auth/login", LoginView.as_view(), name="api-login"),
    path("auth/logout", LogoutView.as_view(), name="api-logout"),
    path("auth/register", RegisterView.as_view(), name="api-register"),
    path("auth/demo-login", DemoLoginView.as_view(), name="api-demo-login"),
    path("auth/reset-password", ResetPasswordView.as_view(), name="api-reset-password"),
    path("me", MeView.as_view(), name="api-me"),
    path("me/password", ChangePasswordView.as_view(), name="api-change-password"),
    path("me/tokens", TokenListCreateView.as_view(), name="api-tokens"),
    path("me/tokens/<str:prefix>", TokenDetailView.as_view(), name="api-token-detail"),
    path("admin/users", AdminUserListView.as_view(), name="api-admin-users"),
    path("admin/users/<str:public_id>", AdminUserDetailView.as_view(), name="api-admin-user"),
    path("admin/users/<str:public_id>/reset-link", ResetLinkView.as_view(), name="api-admin-reset-link"),
]
