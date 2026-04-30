from __future__ import annotations

import importlib


def test_addon_import_and_contract():
    addon=importlib.import_module('client_surfaces.blender.addon')
    assert isinstance(getattr(addon,'bl_info',None),dict)
    assert callable(getattr(addon,'register',None))
    assert callable(getattr(addon,'unregister',None))
