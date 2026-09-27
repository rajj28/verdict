"""Audit log HTML routes (server-rendered page)."""

from django.urls import path

from audit import views

urlpatterns = [
    path("manage/<slug:slug>/audit", views.audit_log, name="manage-audit"),
]
