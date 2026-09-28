"""Test runner that keeps the in-process results preview cache from leaking between tests."""
from django.test.runner import DiscoverRunner

from results.services import clear_preview_cache


class _IsolatedMixin:
    def startTest(self, test):
        clear_preview_cache()
        super().startTest(test)


class PreviewCacheIsolatingRunner(DiscoverRunner):
    def get_resultclass(self):
        base = super().get_resultclass()
        if base is None:
            import unittest
            base = unittest.TextTestResult
        return type("Isolated" + base.__name__, (_IsolatedMixin, base), {})
