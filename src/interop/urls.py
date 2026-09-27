"""Fixture import, JSON round trip and CSV exports.

HTML routes (server-rendered pages).
"""

from django.urls import path

from interop import views

urlpatterns = [
    path("manage/<slug:slug>/webhooks", views.manage_webhooks, name="manage-webhooks"),
    path("manage/<slug:slug>/certificates", views.manage_certificates, name="manage-certificates"),
    path("manage/<slug:slug>/embed", views.manage_embed, name="manage-embed"),
    path("events/<slug:slug>/certificates/<str:kind>/<str:public_id>",
         views.certificate_page, name="certificate"),
    path("certificates/verify/<slug:slug>/<str:kind>/<str:public_id>",
         views.verify_certificate, name="certificate-verify"),
    path(".well-known/verdict-keys.json", views.public_keys, name="verdict-keys"),
    path("records/<str:record_id>", views.judge_record, name="judge-record"),
    path("verify", views.verify_page, name="record-verify-page"),
    path("embed/<slug:slug>", views.embed_gallery, name="embed-gallery"),
    path("embed/<slug:slug>.js", views.embed_script, name="embed-script"),
]
