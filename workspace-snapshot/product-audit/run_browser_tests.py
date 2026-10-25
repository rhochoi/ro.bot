import unittest
from unittest.mock import patch
from playwright.sync_api import BrowserType
original=BrowserType.launch
def launch(self,*a,**kw):
    kw['executable_path']='/usr/bin/chromium'
    return original(self,*a,**kw)
suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.test_pet_ui','tests.test_pet_motion','tests.test_pet_layout','tests.test_pet_feedback'])
with patch.object(BrowserType,'launch',launch):
    result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
