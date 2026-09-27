"""Users, API tokens and authentication.

HTML routes (server-rendered pages). No trailing slashes except /admin-panel/,
which the product spec names with one.
"""
from django.urls import path

from accounts.views import (
    admin_panel,
    dashboard,
    login_page,
    register_page,
    reset_page,
    tokens_page,
)

urlpatterns = [
    path("login", login_page, name="login"),
    path("register", register_page, name="register"),
    path("reset", reset_page, name="reset"),
    path("me", dashboard, name="me"),
    path("me/tokens", tokens_page, name="my_tokens"),
    path("admin-panel/", admin_panel, name="admin_panel"),
]
